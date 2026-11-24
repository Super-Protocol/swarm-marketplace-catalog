#!/usr/bin/env node
// Give the researcher tenant its three aggregate sources, by hand, because the
// listing cannot declare a fourth tenant and therefore cannot ground one.
//
//   APP_BASE=https://<stand> APP_USER=<installation admin> PASS_FILE=./stand.administratorPassword \
//   CARDS=../../../datasets TENANT_NAME="Clinical Research Network" \
//   SOURCES=cypressbay-release,harborpoint-release,ridgeline-release \
//     node researcher-sources.js
//   … node researcher-sources.js --check      # read-only: report, change nothing
//
// WHAT THIS IS A PORT OF. `charts/conversational-analyst-grounding/files/ground.py`
// grounds every mounted card into ONE tenant — the deployment's
// `primaryOrgAlias`, an attested parameter. The researcher tenant was created
// outside the listing (`researcher-tenant.js` says why), so nothing grounds it.
// This does the same three things ground.py does, for that tenant only:
// register the source, read its catalogue, and annotate every table and column
// from the dataset card.
//
// WHY IT REGISTERS BY PROVIDER ID AND NOT BY CONNECTION. Each release source
// already exists in the clinic tenant, created by the grounding Job, and its
// credential lives in a data-source PROVIDER. `adminRegisterDataSource` takes
// `existingProviderId`, so the researcher's source points at the provider the
// Job made: the same gated connection, through the same egress sidecar port, so
// the gate sees every call the researcher makes and the clinic's own access log
// shows it. No credential is read, written or printed here, and this script
// could not leak one if it tried — it never holds one.
//
// WHY IT ANNOTATES FROM THE CARD ON DISK. The catalogue read gives names and
// types. Everything that makes the data usable — what `prep_missing` is for,
// that `withdrawal_minutes_total` must be divided by `withdrawal_recorded`, the
// practice's own coverage caveat — is in the card's descriptions, and the
// marketplace's catalogue endpoint drops `role` and `references` on the way
// out. So the cards are read from the repository, which is what the seeder
// published from. python3 is used as a YAML-to-JSON filter and for nothing else.
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { signIn, graphql } = require('./applogin');

const BASE = required('APP_BASE');
const CARDS = process.env.CARDS || path.join(__dirname, '..', '..', '..', 'datasets');
const TENANT_NAME = process.env.TENANT_NAME || 'Clinical Research Network';
const WANTED = (process.env.SOURCES || 'cypressbay-release,harborpoint-release,ridgeline-release')
  .split(',')
  .map((name) => name.trim())
  .filter(Boolean);
const CHECK_ONLY = process.argv.includes('--check');

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`set ${name}`);
  return value;
}

const say = (...parts) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...parts);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** The dataset name as the grounding Job derives a data source id from it: the
 *  same transformation, so the researcher's source is named what the clinic
 *  tenant's is and a question written against one works against the other. */
const identifier = (name) => name.replace(/[^a-zA-Z0-9]+/g, '_').replace(/^_+|_+$/g, '').toLowerCase();

/** `role` and `references` are the two fields the catalogue's vocabulary needs
 *  and the marketplace's catalogue endpoint does not return, so the card is read
 *  from the repository. python3 is a YAML parser here and nothing more. */
function card(name) {
  const file = path.join(CARDS, name, 'data.yaml');
  if (!fs.existsSync(file)) throw new Error(`no card at ${file} — set CARDS to the datasets directory`);
  const json = execFileSync(
    'python3',
    ['-I', '-c', 'import sys,yaml,json;json.dump(yaml.safe_load(open(sys.argv[1],encoding="utf-8")),sys.stdout)', file],
    { encoding: 'utf8' },
  );
  return JSON.parse(json);
}

/** The same mapping `ground.py` applies, for the same reason: the catalogue
 *  distinguishes a key, a date and a plain category where a card says only
 *  "dimension", so a declared dimension becomes the narrowest thing that is
 *  certainly true of it. Kept identical on purpose — a column that reads as a
 *  measure in one tenant and a category in another is a question that answers
 *  differently depending on who asked. */
function columnRole(column) {
  if (column.role === 'measure') return 'measure';
  if (column.references) return 'key';
  if (['date', 'datetime', 'timestamp'].includes(column.type)) return 'date';
  if (column.role === 'dimension') return 'category';
  return ['integer', 'number'].includes(column.type) ? 'measure' : 'category';
}

const TENANTS = '{ adminTenants { id name } }';
const SOURCES = `{
  adminDataSources(pagination: { limit: 100, offset: 0 }) {
    items { id tenantId datasourceId name dataSourceProviderId schemaTableCount }
  }
}`;
const REGISTER = `mutation ($input: AdminRegisterDataSourceInput!) {
  adminRegisterDataSource(input: $input) { id datasourceId }
}`;
const REFRESH = `mutation ($id: String!, $tenantId: String) {
  adminSchemaRefresh(datasourceId: $id, tenantId: $tenantId) { jobId }
}`;
const REFRESH_STATUS = `query ($jobId: ID!, $tenantId: String) {
  adminSchemaRefreshStatus(jobId: $jobId, tenantId: $tenantId) { state error }
}`;
const TABLES = `query ($id: String, $tenantId: String) {
  adminSchemaTables(datasourceId: $id, tenantId: $tenantId) { id tableName }
}`;
const TABLE = `query ($id: ID!, $tenantId: String) {
  adminSchemaTable(id: $id, tenantId: $tenantId) { columns { id name } }
}`;
const ANNOTATE = `mutation ($input: AdminSchemaAnnotateInput!) { adminSchemaAnnotate(input: $input) }`;
const MESSAGES = '{ adminSystemMessages(pagination: { limit: 100, offset: 0 }) { items { id name tenantId } } }';
const CREATE_MESSAGE = `mutation ($input: AdminCreateSystemMessageInput!) {
  adminCreateSystemMessage(input: $input) { id name }
}`;
const UPDATE_MESSAGE = `mutation ($input: AdminUpdateSystemMessageInput!) {
  adminUpdateSystemMessage(input: $input) { id name }
}`;

/** What the agent is told about the three releases before it starts looking.
 *
 *  Short on purpose, like `ground.py`'s: the catalogue carries the column
 *  descriptions and a second copy of them here is a second copy to keep true.
 *  The one thing this says that the catalogue cannot is the sentence that makes
 *  the pooled question answerable at all — that `network_key` is the same key in
 *  all three sources. */
function domainBrief(cards) {
  const parts = [
    'You answer questions about the data sources registered in this workspace. '
      + 'Discover tables and columns through the knowledge tools — never invent a name.',
    '',
    'Every source here is an AGGREGATE RELEASE published by one clinical network: counts by '
      + 'site, month and procedure type, with no patient, encounter or clinician in it. There is '
      + 'no identifying record in this workspace to find, so a question that needs one cannot be '
      + 'answered here and should be said to be unanswerable rather than approximated.',
    '',
    'The three releases share a schema and a key. `network_key` identifies the network in every '
      + 'table of every source, so a question that covers all three is three queries grouped by '
      + 'that column. Each release also carries its own `*_network_profile` row, whose '
      + '`coverage_note`, `first_procedure_date`, `feed_lag_days` and completeness percentages '
      + 'say what that network\'s numbers can and cannot be compared with. Read it before '
      + 'quoting a rate, and quote the caveat beside the rate.',
    '',
  ];
  for (const [name, definition] of cards) {
    const meta = definition.metadata || {};
    parts.push(`### ${meta.title || name}`);
    parts.push(`Data source id \`${identifier(name)}\`. ${meta.description || ''}`.trim());
    const tables = ((definition.spec || {}).schema || {}).tables || [];
    if (tables.length > 0) {
      parts.push('');
      for (const table of tables) {
        parts.push(table.description ? `- \`${table.name}\` — ${table.description}` : `- \`${table.name}\``);
      }
    }
    parts.push('');
  }
  return parts.join('\n').trim();
}

/** The catalogue read is a worker, not a request: it can be queued behind
 *  another tenant's and the warehouse's dictionary takes its time. The same
 *  patience `ground.py` has, and for the same reason. */
async function waitForCatalogue(ask, sourceId, tenantId, jobId) {
  for (let attempt = 1; attempt <= 60; attempt += 1) {
    const { adminSchemaRefreshStatus: status } = await ask(REFRESH_STATUS, { jobId, tenantId });
    if (status.state === 'failed') {
      throw new Error(`the catalogue read of ${sourceId} failed — ${status.error || 'the source is probably unreachable'}`);
    }
    if (status.state === 'completed') {
      const { adminSchemaTables: tables } = await ask(TABLES, { id: sourceId, tenantId });
      if (tables.length > 0) return tables;
    }
    await sleep(5000);
  }
  throw new Error(`the catalogue of ${sourceId} did not arrive`);
}

(async () => {
  let cookie = null;
  let error = null;
  for (let attempt = 1; attempt <= 5; attempt += 1) {
    try {
      cookie = await signIn({
        base: BASE,
        username: required('APP_USER'),
        password: fs.readFileSync(required('PASS_FILE'), 'utf8').trim(),
      });
      break;
    } catch (failure) {
      error = failure;
      say(`  sign-in ${attempt}: ${failure.message.slice(0, 90)}`);
      await sleep(6000);
    }
  }
  if (!cookie) throw error;
  /** One GraphQL call, retried through a 502.
   *
   *  The stand's oauth2-proxy answers 502 now and then on a perfectly good
   *  request (SUP-917). This script makes a few hundred calls — four tables and
   *  twenty columns per source — so at that rate a transient gateway failure is
   *  not unlikely, and losing a run to one means re-annotating everything. Only
   *  a gateway failure is retried: a GraphQL error is the server disagreeing
   *  with the request and will disagree again. */
  const ask = async (query, variables) => {
    let last = null;
    for (let attempt = 1; attempt <= 5; attempt += 1) {
      try {
        return await graphql({ base: BASE, cookie, query, variables });
      } catch (failure) {
        last = failure;
        if (!/\b50[0-9]\b/.test(failure.message)) throw failure;
        say(`  gateway ${failure.message.slice(0, 40).replace(/\s+/g, ' ')} — retrying (${attempt})`);
        await sleep(4000);
      }
    }
    throw last;
  };

  const { adminTenants: tenants } = await ask(TENANTS);
  const tenant = tenants.find((candidate) => candidate.name === TENANT_NAME);
  if (!tenant) {
    throw new Error(
      `no tenant called ${JSON.stringify(TENANT_NAME)} — `
        + `the stand has ${tenants.map((t) => JSON.stringify(t.name)).join(', ')}. `
        + 'Create it with researcher-tenant.js first.',
    );
  }
  say(`${CHECK_ONLY ? 'checking' : 'grounding'} ${TENANT_NAME} (${tenant.id})`);

  const { adminDataSources: all } = await ask(SOURCES);
  const cards = WANTED.map((name) => [name, card(name)]);

  for (const [name] of cards) {
    const sourceId = identifier(name);
    const mine = all.items.find((item) => item.tenantId === tenant.id && item.datasourceId === sourceId);
    // The provider the grounding Job created for the clinic tenant: the gated
    // connection and its credential. Taken by id and never read.
    const theirs = all.items.find((item) => item.tenantId !== tenant.id && item.datasourceId === sourceId);
    if (!mine && !theirs) {
      say(`  ${sourceId}: nowhere on this stand — is ${name} mounted on the deployment?`);
      process.exitCode = 1;
      continue;
    }
    if (mine) {
      say(`  ${sourceId}: already registered, ${mine.schemaTableCount} table(s) in its catalogue`);
      continue;
    }
    if (CHECK_ONLY) {
      say(`  ${sourceId}: MISSING from this tenant`);
      process.exitCode = 1;
      continue;
    }
    await ask(REGISTER, {
      input: {
        tenantId: tenant.id,
        datasourceId: sourceId,
        name: sourceId,
        type: 'clickhouse',
        description: (card(name).metadata || {}).title || name,
        isActive: true,
        existingProviderId: theirs.dataSourceProviderId,
        // Required by the schema and ignored by the service when a provider is
        // named: `register` parses `config` only on the branch that has to
        // create a provider (`admin-data-source.service.ts`, `if
        // (!input.existingProviderId)`). An empty object here is the honest
        // value — this script has no connection to declare, which is the point.
        config: '{}',
      },
    });
    say(`  ${sourceId}: registered against the provider the grounding Job made (${theirs.dataSourceProviderId})`);
  }
  if (CHECK_ONLY) {
    say('checked');
    return;
  }

  say('the catalogue, and the card\'s own words over it');
  for (const [name, definition] of cards) {
    const sourceId = identifier(name);
    const declared = new Map(
      (((definition.spec || {}).schema || {}).tables || []).map((table) => [table.name, table]),
    );
    const { adminSchemaRefresh: job } = await ask(REFRESH, { id: sourceId, tenantId: tenant.id });
    const tables = await waitForCatalogue(ask, sourceId, tenant.id, job.jobId);
    let annotated = 0;
    for (const table of tables) {
      const spec = declared.get(table.tableName);
      if (!spec) continue;
      if (spec.description) {
        await ask(ANNOTATE, {
          input: { target: 'table', id: table.id, tenantId: tenant.id, description: spec.description },
        });
        annotated += 1;
      }
      const { adminSchemaTable: detail } = await ask(TABLE, { id: table.id, tenantId: tenant.id });
      const columns = new Map(detail.columns.map((column) => [column.name, column.id]));
      for (const column of spec.columns || []) {
        const id = columns.get(column.name);
        if (!id) continue;
        const input = { target: 'column', id, tenantId: tenant.id, role: columnRole(column) };
        if (column.description) input.description = column.description;
        await ask(ANNOTATE, { input });
        annotated += 1;
      }
    }
    say(`  ${sourceId}: ${tables.length} table(s) in the catalogue, ${annotated} annotation(s) applied`);
  }

  say('the system message');
  const { adminSystemMessages: messages } = await ask(MESSAGES);
  const existing = messages.items.find(
    (message) => message.name === 'Domain part' && message.tenantId === tenant.id,
  );
  const payload = { name: 'Domain part', index: 10, text: domainBrief(cards), tenantId: tenant.id };
  if (existing) {
    await ask(UPDATE_MESSAGE, { input: { id: existing.id, ...payload } });
    say("  updated 'Domain part' for this tenant");
  } else {
    await ask(CREATE_MESSAGE, { input: payload });
    say("  created 'Domain part' for this tenant");
  }

  say('the researcher tenant is grounded on its three releases');
})().catch((failure) => {
  console.log('FAILED', failure.message);
  process.exit(1);
});
