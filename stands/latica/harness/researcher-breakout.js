#!/usr/bin/env node
// The attack the aggregate release has to survive, carried out rather than
// argued about.
//
//   APP_BASE=https://<stand> APP_USER=research-network-admin@local.test \
//   PASS_FILE=./researcher.password \
//   TARGET_HOST=conversational-analyst-gatekeeper-egress TARGET_PORT=9506 \
//   TARGET_USER=cbdh_reader TARGET_TABLE=marketplace.cbdh_patients \
//     node researcher-breakout.js
//
// THE CLAIM UNDER TEST. The researcher tenant holds three aggregate releases and
// no identifying card. But the identifying card IS mounted on this deployment —
// Cypress Bay's own, in its own tenant — and the egress sidecar's listener for
// it is on a port that is written in a ConfigMap anybody in the namespace can
// read. `admin.data-sources.write` is a permission a tenant's OWN administrator
// holds, by a deliberate decision with its reasoning in the resolver: "whoever
// may save a connection and check it can already make the same socket open".
//
// So the researcher can point a new data source at the clinic's gate port. What
// has to stop them is the layer below the agent: ClickHouse wants the reader's
// password, and the researcher has never held it. This script tries the whole
// sequence — register, read the catalogue, query a table by name — and reports
// where it stopped. Anything other than "stopped at authentication" is a finding
// bigger than the issue this was written for.
//
// It CLEANS UP. The source it registers is unregistered again in a `finally`,
// including when a step throws, because leaving a half-registered source on
// somebody's stand is worse than not running the test.
const fs = require('node:fs');
const { signIn, graphql } = require('./applogin');

const BASE = required('APP_BASE');
const HOST = process.env.TARGET_HOST || 'conversational-analyst-gatekeeper-egress';
const PORT = Number(process.env.TARGET_PORT || 9506);
const USER = process.env.TARGET_USER || 'cbdh_reader';
const DATABASE = process.env.TARGET_DATABASE || 'marketplace';
const TABLE = process.env.TARGET_TABLE || 'marketplace.cbdh_patients';
const PASSWORD = process.env.TARGET_PASSWORD || 'the-researcher-does-not-have-this';
const SOURCE_ID = process.env.PROBE_SOURCE_ID || 'breakout_probe';
/** Leave the probe source registered, so a dialog turn can be aimed at it and
 *  the question "can it read ROWS, not just names" can be answered. Clean it up
 *  by hand afterwards — the script says how. */
const KEEP = process.argv.includes('--keep');

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`set ${name}`);
  return value;
}

const say = (...parts) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...parts);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// The caller's own tenant id, which `adminRegisterDataSource` requires and
// `tenantScope.assertTenant` will not let a tenant administrator fake. Read off
// the sources this tenant already has rather than passed in, so the probe cannot
// accidentally be run against somebody else's tenant.
const WHOAMI = `{
  datasources { id name }
  adminDataSources(pagination: { limit: 5, offset: 0 }) { items { tenantId } }
}`;
const TEST = `mutation ($input: AdminTestDataSourceConfigInput!) {
  adminTestDataSourceConfig(input: $input) { ok error failure detail }
}`;
const REGISTER = `mutation ($input: AdminRegisterDataSourceInput!) {
  adminRegisterDataSource(input: $input) { id tenantId datasourceId }
}`;
const UNREGISTER = 'mutation ($id: ID!) { adminUnregisterDataSource(id: $id, deleteProviderIfUnused: true) }';
const REFRESH = `mutation ($id: String!) { adminSchemaRefresh(datasourceId: $id) { jobId } }`;
const REFRESH_STATUS = 'query ($jobId: ID!) { adminSchemaRefreshStatus(jobId: $jobId) { state error } }';
const TABLES = 'query ($id: String) { adminSchemaTables(datasourceId: $id) { tableName } }';
const CHECK = 'mutation ($id: ID!) { adminCheckDataSource(id: $id) { ok error failure detail } }';

(async () => {
  let cookie = null;
  let failure = null;
  for (let attempt = 1; attempt <= 5; attempt += 1) {
    try {
      cookie = await signIn({
        base: BASE,
        username: required('APP_USER'),
        password: fs.readFileSync(required('PASS_FILE'), 'utf8').trim(),
      });
      break;
    } catch (error) {
      failure = error;
      await sleep(6000);
    }
  }
  if (!cookie) throw failure;
  const ask = async (query, variables) => {
    try {
      return [await graphql({ base: BASE, cookie, query, variables }), null];
    } catch (error) {
      return [null, error.message.replace(/\s+/g, ' ').slice(0, 200)];
    }
  };

  if (process.argv.includes('--remove')) {
    const [found, foundError] = await ask(
      '{ adminDataSources(pagination: { limit: 50, offset: 0 }) { items { id datasourceId } } }',
    );
    if (!found) throw new Error(`cannot list this tenant's sources — ${foundError}`);
    const probe = found.adminDataSources.items.find((item) => item.datasourceId === SOURCE_ID);
    if (!probe) {
      say(`${SOURCE_ID} is not registered in this tenant — nothing to remove`);
      return;
    }
    const [, removeError] = await ask(UNREGISTER, { id: probe.id });
    say(removeError ? `COULD NOT UNREGISTER — ${removeError}` : `unregistered ${SOURCE_ID}`);
    if (removeError) process.exitCode = 1;
    return;
  }

  const [mine, mineError] = await ask(WHOAMI);
  if (!mine) throw new Error(`cannot read this tenant's own sources — ${mineError}`);
  say(`signed in as ${process.env.APP_USER}; this tenant holds ${JSON.stringify(mine.datasources.map((d) => d.name))}`);
  const tenantId = process.env.TENANT_ID
    || (mine.adminDataSources.items[0] && mine.adminDataSources.items[0].tenantId);
  if (!tenantId) throw new Error('this tenant has no source to read its own id from — set TENANT_ID');
  say(`tenant ${tenantId}`);
  say(`target: ${HOST}:${PORT} as ${USER}, reading ${TABLE}`);

  const config = JSON.stringify({
    host: HOST, port: PORT, protocol: 'http:', database: DATABASE, username: USER, password: PASSWORD,
  });

  // 1. the wizard's Test button
  const [tested, testError] = await ask(TEST, { input: { type: 'clickhouse', config } });
  say(
    '1. test the connection    : '
      + (tested
        ? `ok=${tested.adminTestDataSourceConfig.ok} ${(tested.adminTestDataSourceConfig.failure || tested.adminTestDataSourceConfig.error || '').replace(/\s+/g, ' ').slice(0, 110)}`
        : `the call was refused — ${testError}`),
  );

  // 2. register it for real
  const [registered, registerError] = await ask(REGISTER, {
    input: {
      tenantId,
      datasourceId: SOURCE_ID,
      name: SOURCE_ID,
      type: 'clickhouse',
      config,
      isActive: true,
    },
  });
  if (!registered) {
    say(`2. register it            : the call was refused — ${registerError}`);
    say('');
    say('It stopped before registering anything. Nothing to clean up.');
    return;
  }
  const link = registered.adminRegisterDataSource;
  say(`2. register it            : registered as ${link.datasourceId} in tenant ${link.tenantId}`);

  try {
    // 3. does the saved source actually connect?
    const [checked, checkError] = await ask(CHECK, { id: link.id });
    say(
      '3. check the saved source : '
        + (checked
          ? `ok=${checked.adminCheckDataSource.ok} ${(checked.adminCheckDataSource.failure || checked.adminCheckDataSource.error || checked.adminCheckDataSource.detail || '').replace(/\s+/g, ' ').slice(0, 140)}`
          : `refused — ${checkError}`),
    );

    // 4. can it read the clinic's dictionary?
    const [job, jobError] = await ask(REFRESH, { id: SOURCE_ID });
    let state = jobError ? `refused — ${jobError}` : 'enqueued';
    if (job) {
      for (let attempt = 1; attempt <= 24; attempt += 1) {
        await sleep(5000);
        const [status] = await ask(REFRESH_STATUS, { jobId: job.adminSchemaRefresh.jobId });
        if (!status) continue;
        state = `${status.adminSchemaRefreshStatus.state} ${(status.adminSchemaRefreshStatus.error || '').replace(/\s+/g, ' ').slice(0, 140)}`.trim();
        if (!['running', 'pending', 'queued'].includes(status.adminSchemaRefreshStatus.state)) break;
      }
    }
    say(`4. read its catalogue     : ${state}`);
    const [tables] = await ask(TABLES, { id: SOURCE_ID });
    const names = tables ? tables.adminSchemaTables.map((t) => t.tableName) : [];
    say(`   tables discovered      : ${names.length}${names.length ? ` ${JSON.stringify(names.slice(0, 5))}` : ''}`);
    if (names.length > 0) {
      say('');
      say('!! THE RESEARCHER READ THE CLINIC\'S DICTIONARY. Stop and report this.');
      process.exitCode = 1;
    }
  } finally {
    if (KEEP) {
      say(`5. clean up               : SKIPPED (--keep). ${SOURCE_ID} is still registered in ${link.tenantId}.`);
      say(`   remove it with: PROBE_SOURCE_ID=${SOURCE_ID} node ${require('node:path').basename(__filename)} --remove`);
      return;
    }
    const [, cleanupError] = await ask(UNREGISTER, { id: link.id });
    say(`5. clean up               : ${cleanupError ? `COULD NOT UNREGISTER — ${cleanupError}` : `unregistered ${SOURCE_ID}`}`);
    if (cleanupError) process.exitCode = 1;
  }
})().catch((error) => {
  console.log('FAILED', error.message);
  process.exit(1);
});
