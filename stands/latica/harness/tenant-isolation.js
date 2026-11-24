#!/usr/bin/env node
// The isolation table: the same questions, asked by each tenant in turn.
//
//   APP_BASE=https://<stand> \
//   PEOPLE='[{"label":"Clinic","user":"a@local.test","passFile":"./a.pass"}, …]' \
//     node tenant-isolation.js
//
// What one party can see of another's data is the claim this stand exists to
// make, so it is asked rather than asserted. Four questions, and the fourth is
// the one that matters: given the DATASOURCE ID of a clinic's own records —
// which is not a secret, the operator knows it — can this tenant read that
// source's table list, and can it run SQL against it?
//
// `datasources` and `adminDataSources` are tenant-scoped by the agent. The
// probe exists because scoping that holds for a list does not automatically
// hold for a lookup by id, and a lookup by id is what an attacker with the name
// would try.
const fs = require('node:fs');
const { signIn, graphql } = require('./applogin');

const BASE = required('APP_BASE');
const PEOPLE = JSON.parse(required('PEOPLE'));
/** Data source ids to probe by name. Not secrets: they are derived from the
 *  published dataset names, so anybody reading the catalogue can guess them. */
const PROBES = (process.env.PROBE_SOURCES
  || 'cypressbay_digestive_health,harborpoint_gastroenterology,ridgeline_endoscopy').split(',');

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`set ${name}`);
  return value;
}

/** The probe that matters once the researcher holds only aggregates.
 *
 *  The boundary this stand claims is "the researcher sees no raw record of any
 *  single clinic", and the thing that makes it true is that the sources the
 *  researcher holds contain no raw records. So the attack is not a query — it is
 *  the researcher CONNECTING SOMETHING ELSE: the egress sidecar's listener for
 *  the clinic's identifying card is on a port anybody can read off the
 *  ConfigMap, and `admin.data-sources.write` is a permission a tenant's own
 *  administrator holds.
 *
 *  `adminTestDataSourceConfig` is the wizard's Test button, and its own resolver
 *  comment says the installation accepted knowingly that a tenant administrator
 *  may open a socket to an address they supply. What has to stop them is the
 *  layer below: ClickHouse itself, which wants `cbdh_reader`'s password, and the
 *  researcher does not have it. A result of `ok: true` here would be a finding
 *  larger than this issue.
 *
 *  Set PROBE_CONNECT to run it. It is a mutation in name only — it stores
 *  nothing — but it does make the server open a connection, so it is opt-in. */
const CONNECT = `mutation ($input: AdminTestDataSourceConfigInput!) {
  adminTestDataSourceConfig(input: $input) { ok error failure detail }
}`;

/** And the other half of the same question: can the researcher simply READ the
 *  credential out of a connection it has been given, or out of one it has not?
 *  `adminDataSourceProvider` returns `config`, and `config` carries the
 *  password.
 *
 *  `config` IS requested, because asking for the harmless fields would test
 *  nothing. It is reduced to two booleans the moment it arrives and the string
 *  itself is never printed, logged or returned — a probe for a credential leak
 *  must not be one. */
const PROVIDER = 'query ($id: ID!) { adminDataSourceProvider(id: $id) { id name type config } }';

const OWN = '{ datasources { id name } }';
const ADMIN = `{
  adminDataSources(pagination: { limit: 50, offset: 0 }) { items { datasourceId tenantId } }
  adminSystemMessages(pagination: { limit: 50, offset: 0 }) { items { name tenantId } }
}`;
const MODELS = '{ adminLlmModels(pagination: { limit: 5, offset: 0 }) { items { name } } }';
const TABLES = 'query ($id: String) { adminSchemaTables(datasourceId: $id) { tableName } }';

(async () => {
  for (const person of PEOPLE) {
    const password = fs.readFileSync(person.passFile, 'utf8').trim();
    let cookie = null;
    let signInError = null;
    for (let attempt = 1; attempt <= 4; attempt += 1) {
      try { cookie = await signIn({ base: BASE, username: person.user, password }); break; }
      catch (error) { signInError = error.message.slice(0, 90); await new Promise((r) => setTimeout(r, 6000)); }
    }
    console.log(`\n== ${person.label}  (${person.user})`);
    if (!cookie) { console.log(`   cannot sign in: ${signInError}`); continue; }

    const ask = async (query, variables) => {
      try { return [await graphql({ base: BASE, cookie, query, variables }), null]; }
      catch (error) { return [null, error.message.replace(/\s+/g, ' ').slice(0, 110)]; }
    };

    const [own, ownError] = await ask(OWN);
    console.log(`   own datasources     : ${own ? JSON.stringify(own.datasources.map((d) => d.name)) : `refused — ${ownError}`}`);

    const [admin, adminError] = await ask(ADMIN);
    if (admin) {
      console.log(`   adminDataSources    : ${JSON.stringify(admin.adminDataSources.items.map((i) => i.datasourceId))}`);
      console.log(`   adminSystemMessages : ${JSON.stringify(admin.adminSystemMessages.items.map((i) => i.name))}`);
    } else {
      console.log(`   admin reads         : refused — ${adminError}`);
    }

    const [models, modelsError] = await ask(MODELS);
    console.log(`   model registry      : ${models ? JSON.stringify(models.adminLlmModels.items.map((m) => m.name)) : `refused — ${modelsError}`}`);

    for (const id of PROBES) {
      const [tables, tablesError] = await ask(TABLES, { id });
      const names = tables ? tables.adminSchemaTables.map((t) => t.tableName) : null;
      console.log(`   tables of ${id.padEnd(30)}: ${names ? `${names.length} table(s)${names.length ? ' ' + JSON.stringify(names.slice(0, 3)) + '…' : ''}` : `refused — ${tablesError}`}`);
    }

    if (process.env.PROBE_CONNECT) {
      for (const target of JSON.parse(process.env.PROBE_CONNECT)) {
        const [result, connectError] = await ask(CONNECT, {
          input: {
            type: 'clickhouse',
            config: JSON.stringify({
              host: target.host,
              port: target.port,
              protocol: 'http:',
              database: target.database || 'marketplace',
              username: target.username,
              password: target.password,
            }),
          },
        });
        const verdict = result
          ? result.adminTestDataSourceConfig.ok
            ? '!! CONNECTED — this is a finding, stop and report it'
            : `refused — ${(result.adminTestDataSourceConfig.failure
                || result.adminTestDataSourceConfig.error
                || result.adminTestDataSourceConfig.detail
                || 'no reason given').replace(/\s+/g, ' ').slice(0, 120)}`
          : `the call itself was refused — ${connectError}`;
        console.log(`   connecting ${target.label.padEnd(26)}: ${verdict}`);
        if (result && result.adminTestDataSourceConfig.ok) process.exitCode = 1;
      }
    }

    if (process.env.PROBE_PROVIDERS) {
      for (const provider of JSON.parse(process.env.PROBE_PROVIDERS)) {
        const [read, readError] = await ask(PROVIDER, { id: provider.id });
        let verdict;
        if (!read) {
          verdict = `refused — ${readError}`;
        } else {
          const found = read.adminDataSourceProvider;
          let password = false;
          try {
            password = Boolean(JSON.parse(found.config || '{}').password);
          } catch {
            password = /password/.test(found.config || '');
          }
          verdict = `!! READABLE as ${found.name}, password present = ${password}`;
          if (password) process.exitCode = 1;
        }
        console.log(`   provider ${provider.label.padEnd(27)}: ${verdict}`);
      }
    }
  }
  console.log('');
})().catch((error) => { console.log('FAILED', error.message); process.exit(1); });
