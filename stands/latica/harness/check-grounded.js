#!/usr/bin/env node
// Did the deployment actually START WORKING after a window, as opposed to
// attesting correctly?
//
//   APP_BASE=… APP_USER=… PASS_FILE=… MP_ORG=… MP_DEPLOYMENT=… node check-grounded.js
//   … MP_WAIT_MIN=30 node check-grounded.js            # how long to allow (default 30)
//   … MP_HOST_SSH=user@host node check-grounded.js     # also read the gate's verdicts
//
// `mount-datasets.js` proves a window did not break the attestation: the digest
// did not move, it still matches what the listing declares, the hostname serves
// the bundle. All of that was true on the 2026-11-24 window while the two cards
// it had just mounted were invisible to the agent — because mounting a dataset
// only puts it in the bundle, and what turns it into something the agent can
// answer about is the grounding Job, which is not collected into evidence, has
// no status the cloud will show from outside a published space, and legitimately
// takes a long time: `wait_for` gives the API 15 minutes before it gives up, and
// the pod then restarts with up to 6 minutes of backoff.
//
// So this is the other half, and it is deliberately a SEPARATE script with a
// separate verdict. Three facts, in the order they become true:
//
//   1. the agent's own API answers at all;
//   2. every mounted card is a registered data source, under the id the
//      grounding Job derives from the dataset name — and the system message
//      names it, which is the independent second signal, because `ensure_prompt`
//      rewrites that message from the bindings at the end of the run;
//   3. the gate has logged an `allowed` verdict for each mounted card since the
//      window, which is the only thing that says data is really being served
//      rather than merely registered.
//
// **A failure here is not a reason to roll back.** Rollback is for an
// attestation failure — a digest that moved, a workload that is down — where
// the stand is not serving what it says it serves. A slow or failed grounding is
// diagnosed with the stand up and the clinic's own data still flowing.
const fs = require('node:fs');
const { api } = require('./marketplace');
const { signIn, graphql } = require('./applogin');
const { execFileSync } = require('node:child_process');

const ORG = required('MP_ORG');
const DEPLOYMENT = required('MP_DEPLOYMENT');
const BASE = process.env.APP_BASE;
const WAIT_MS = Number(process.env.MP_WAIT_MIN || 30) * 60 * 1000;
const SINCE = process.env.MP_SINCE || new Date(Date.now() - 60 * 60 * 1000).toISOString();

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`set ${name}`);
  return value;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const say = (...parts) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...parts);

/** The same transform `ground.py` applies: a dataset name is a catalogue slug, a
 *  data source id has to be a SQL-safe identifier. Kept in step with
 *  `identifier()` there — if that changes, this is wrong and silently so. */
const identifier = (name) => name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');

async function connect() {
  const password = fs.readFileSync(required('PASS_FILE'), 'utf8').trim();
  // Retried: the sign-in path through oauth2-proxy answers 502 intermittently on
  // this stand, and a single attempt turns that into a false verdict.
  for (let attempt = 1; ; attempt += 1) {
    try {
      return await signIn({ base: BASE, username: required('APP_USER'), password });
    } catch (error) {
      if (attempt >= 6) throw error;
      await sleep(8000);
    }
  }
}

const Q_SOURCES = 'query { adminDataSources { items { datasourceId } } }';
const Q_MESSAGES = 'query { adminSystemMessages(pagination: { limit: 100, offset: 0 }) { items { name text } } }';

/** The gate's own verdicts, which live in the marketplace's database and belong
 *  to the DATASET's owner rather than to the deploying organisation — so there is
 *  no API call this script can make for them. Read over ssh when the caller says
 *  where, and otherwise reported as unchecked with the query to run. */
function verdicts(names) {
  const host = process.env.MP_HOST_SSH;
  const sql = `select l.name, e.verdict, count(*) from gatekeeper_access_events e `
    + `join dataset_listings l on l.id = e."datasetListingId" `
    + `where e."deploymentId" = '${DEPLOYMENT}' and e."occurredAt" > '${SINCE}' group by 1,2`;
  if (!host) {
    say('  not checked — set MP_HOST_SSH to read them. On the marketplace host:');
    say(`    docker exec marketplace-postgres-1 psql -U marketplace -d swarm_marketplace -c "${sql}"`);
    return null;
  }
  const out = execFileSync('ssh', [
    '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=no', host,
    `docker exec marketplace-postgres-1 psql -U marketplace -d swarm_marketplace -At -F'|' -c ${JSON.stringify(sql)}`,
  ], { encoding: 'utf8', timeout: 120000 });
  const seen = new Map();
  for (const line of out.trim().split('\n').filter(Boolean)) {
    const [name, verdict, count] = line.split('|');
    say(`  ${name}: ${verdict} ×${count}`);
    if (verdict === 'allowed') seen.set(name, Number(count));
  }
  return names.filter((name) => !seen.has(name));
}

(async () => {
  const [status, deployment] = await api('GET', `/api/v1/organizations/${ORG}/deployments/${DEPLOYMENT}`);
  if (status >= 300) throw new Error(`GET deployment ${status}`);
  const mounted = (deployment.datasets || []).map((d) => d.datasetName);
  const wanted = mounted.map(identifier);
  say(`${deployment.appTitle} ${deployment.appVersion} — mounted: ${JSON.stringify(mounted)}`);
  say(`expecting data sources: ${wanted.join(', ')}`);

  const problems = [];

  say('1. the agent API answers');
  const cookie = await connect();
  say('   signed in');

  say(`2. every mounted card is registered and named in the system message (up to ${WAIT_MS / 60000} min)`);
  const deadline = Date.now() + WAIT_MS;
  let missing = wanted;
  let named = [];
  for (;;) {
    const sources = (await graphql({ base: BASE, cookie, query: Q_SOURCES })).adminDataSources.items
      .map((s) => s.datasourceId);
    missing = wanted.filter((id) => !sources.includes(id));
    const domain = ((await graphql({ base: BASE, cookie, query: Q_MESSAGES })).adminSystemMessages.items
      .find((m) => m.name === 'Domain part') || {}).text || '';
    named = wanted.filter((id) => domain.includes(`\`${id}\``));
    say(`   sources ${sources.length}/${wanted.length}, named in the prompt ${named.length}/${wanted.length}`);
    if (missing.length === 0 && named.length === wanted.length) break;
    if (Date.now() >= deadline) break;
    await sleep(60000);
  }
  if (missing.length > 0) problems.push(`never registered as a data source: ${missing.join(', ')}`);
  const unnamed = wanted.filter((id) => !named.includes(id));
  if (unnamed.length > 0) problems.push(`not named in the Domain part system message: ${unnamed.join(', ')}`);

  say(`3. the gate has allowed each card since ${SINCE}`);
  const unserved = verdicts(mounted);
  if (unserved && unserved.length > 0) problems.push(`no allowed verdict since the window: ${unserved.join(', ')}`);

  console.log('');
  if (problems.length === 0) {
    say('the deployment is grounded on every mounted card and the gate is serving all of them');
    return;
  }
  for (const problem of problems) say(`!! ${problem}`);
  say('');
  say('This is a FUNCTIONAL failure, not an attestation one. Do NOT roll back for it:');
  say('the stand is up, its evidence is intact and the cards it was already serving');
  say('are still being served. Diagnose it with the stand running.');
  process.exit(1);
})().catch((error) => {
  console.log('FAILED', error.message);
  process.exit(1);
});
