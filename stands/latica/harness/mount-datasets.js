#!/usr/bin/env node
// Mount a different set of dataset cards on the analyst stand, in one
// `unpublish → reconfigure → publish` window, and prove afterwards that the
// stand came back to the state it was in.
//
//   MP_ORG=… MP_DEPLOYMENT=… node mount-datasets.js                       # dry run, the default
//   MP_ORG=… MP_DEPLOYMENT=… node mount-datasets.js --apply --i-have-a-go
//   MP_ORG=… MP_DEPLOYMENT=… node mount-datasets.js --rollback --i-have-a-go
//
// WHY THIS EXISTS AT ALL. A gated dataset reaches a deployment three ways at
// once and `reconfigure` is the only writer of all three: `datasetsJson`, from
// which the gatekeeper derives a client's admitted sources; the egress
// sidecar's nginx and Service, one listener per mounted dataset; and the
// grounding Job, whose name is a hash of the bindings. The last two are
// workloads, and a published cluster space refuses every workload change — so
// there is no way to add a card to a running deployment without taking it off
// the air for the length of two pod bring-ups.
//
// WHAT IT REFUSES TO GET WRONG. `reconfigure` replaces the parameter set
// wholesale, and the parameter set is attested: a value that drifts moves the
// evidence digest, and a moved digest costs this deployment its access to every
// gated dataset it has, because the gate holds it to the digest its listing
// declares. So this script never carries a parameter literal. It reads the live
// deployment's own `parameters` back from the marketplace and sends exactly
// those, and it writes them to a recovery file before it touches anything.
//
// Sensitive parameters are deliberately absent rather than blank: the API reads
// a missing sensitive value as "keep", which is what is wanted — the internal
// credential, the embeddings key and the model key are not being changed, and
// regenerating the credential the databases were initialised with would take
// the deployment down.
//
// The digest does not move for a dataset change, and that is checked rather
// than assumed: the two collected sidecar objects that do change carry
// `swarm.io/exclude-evidence-fields` for exactly the fields that change
// (`/data/nginx.conf` on the ConfigMap, `/spec/ports` on the Service), and the
// grounding Job is not collected at all. Step 6 below compares the digest the
// cloud publishes after the window with the one the deployment is held to, and
// fails loudly if they differ.
const fs = require('node:fs');
const path = require('node:path');
const { api } = require('./marketplace');

const ORG = required('MP_ORG');
const DEPLOYMENT = required('MP_DEPLOYMENT');
const BASE = `/api/v1/organizations/${ORG}/deployments/${DEPLOYMENT}`;
const STATE = process.env.MP_STATEFILE || path.join(__dirname, `.mount-${DEPLOYMENT}.json`);

// The cards to mount, in the order a reader of the stand should meet them. One
// slot, because the Conversational Analyst listing declares one (`datasets`,
// `multiple: true`).
const SLOT = process.env.MP_SLOT || 'datasets';
const WANTED = (process.env.MP_DATASETS
  || 'cypressbay-digestive-health,harborpoint-gastroenterology,ridgeline-endoscopy')
  .split(',')
  .map((name) => name.trim())
  .filter(Boolean);

const apply = process.argv.includes('--apply');
const rollback = process.argv.includes('--rollback');
const authorised = process.argv.includes('--i-have-a-go');

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`set ${name}`);
  return value;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const stamp = () => new Date().toISOString().slice(11, 19);
const say = (...parts) => console.log(`[${stamp()}]`, ...parts);

async function deployment() {
  const [status, body] = await api('GET', BASE);
  if (status >= 300 || !body) throw new Error(`GET deployment ${status}`);
  return body;
}

/** Everything needed to put the deployment back, written before anything moves. */
function record(live) {
  const state = {
    recordedAt: new Date().toISOString(),
    deploymentId: live.id,
    appVersion: live.appVersion,
    published: live.published,
    expectedEvidenceDigest: live.expectedEvidenceDigest ?? null,
    renderDigest: live.renderDigest ?? null,
    endpoints: live.endpoints ?? [],
    parameters: live.parameters,
    datasets: live.datasets,
  };
  fs.writeFileSync(STATE, JSON.stringify(state, null, 1), { mode: 0o600 });
  say(`recovery state written to ${path.basename(STATE)}`);
  return state;
}

/** The same two conditions `grants.isBindable` applies, asked of the catalogue
 *  before the stand goes down rather than discovered by a refused reconfigure.
 *
 *  `accessible` is the grant to THIS organisation — the one that owns the
 *  deployment, not the one the data describes — and it is only computed when
 *  `organizationId` is passed, so a call without it reads `false` on a dataset
 *  that is perfectly bindable. `compatibleApps` carries the grant to the
 *  application together with the slot its interface satisfies. A dataset the
 *  catalogue does not serve at all answers 404, which is what an unpublished
 *  card looks like from here. */
async function checkCards(names, { organizationId, appName }) {
  let ok = true;
  for (const name of names) {
    const [status, card] = await api(
      'GET',
      `/api/v1/catalog/datasets/${name}?organizationId=${encodeURIComponent(organizationId)}`,
    );
    const published = status < 300 && Boolean(card);
    const granted = published && card.accessible === true;
    const gated = published && card.accessMode === 'gateway';
    const forApp = published && (card.compatibleApps || []).find((entry) => entry.name === appName);
    say(
      `  ${name}: published=${published} grantedToUs=${granted} gated=${gated} `
        + `slotIn${appName}=${forApp ? forApp.slot : 'none'}`,
    );
    if (!published || !granted || !gated || !forApp) ok = false;
  }
  return ok;
}

async function waitFor(what, predicate, { tries = 30, every = 10000 } = {}) {
  for (let attempt = 1; attempt <= tries; attempt += 1) {
    const live = await deployment();
    if (predicate(live)) return live;
    say(`  waiting for ${what}: status=${live.status} published=${live.published}`);
    await sleep(every);
  }
  throw new Error(`${what} did not happen inside the window`);
}

/** The hostname list is filled by a worker that runs once a minute, so publishing
 *  on the first read publishes a deployment that is dark (SUP-459). */
async function dnsReady() {
  for (let attempt = 1; attempt <= 15; attempt += 1) {
    await api('POST', `${BASE}/dns`);
    const [, hostnames] = await api('GET', `${BASE}/hostnames`);
    const all = hostnames || [];
    const ready = all.length > 0 && all.every((h) => h.verified && h.delegated);
    say('  hostnames', JSON.stringify(all.map((h) => [h.hostname, h.verified, h.delegated])));
    if (ready) return all;
    await sleep(20000);
  }
  throw new Error('hostnames never verified — do not publish a deployment that is dark');
}

/** The digest the cloud publishes, against the one the deployment is held to. */
async function checkEvidence(expected) {
  for (let attempt = 1; attempt <= 25; attempt += 1) {
    const [, evidence] = await api('GET', `${BASE}/evidence`);
    if (evidence && evidence.digest) {
      const resources = (evidence.snapshot && evidence.snapshot.resources) || [];
      const kinds = {};
      for (const resource of resources) kinds[resource.kind] = (kinds[resource.kind] || 0) + 1;
      say('  digest         ', evidence.digest);
      say('  matchesExpected', evidence.matchesExpected);
      say('  resources      ', resources.length, JSON.stringify(kinds));
      if (expected && evidence.digest !== expected) {
        say(`  !! the digest MOVED — it was ${expected}`);
        return { evidence, moved: true };
      }
      return { evidence, moved: false };
    }
    say('  no evidence published yet');
    await sleep(20000);
  }
  throw new Error('no evidence inside the window');
}

/** The same bundle as the gate reads, from the hostname a consumer uses. */
async function checkLiveEvidence(host, expected) {
  const response = await fetch(`https://${host}/.well-known/swarm-evidence`, {
    signal: AbortSignal.timeout(20000),
  });
  if (!response.ok) throw new Error(`/.well-known/swarm-evidence ${response.status}`);
  const bundle = await response.json();
  const payload = JSON.parse(
    Buffer.from(String(bundle.jws).split('.')[1].replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString('utf8'),
  );
  const agrees = !expected || payload.evidenceDigest === expected;
  say(`  live bundle digest ${payload.evidenceDigest} agrees=${agrees}`);
  return agrees;
}

async function reconfigure(parameters, datasets) {
  const [status, body] = await api('POST', `${BASE}/reconfigure`, { parameters, datasets });
  say('reconfigure', status, JSON.stringify(body && { status: body.status, message: body.statusMessage }));
  if (status >= 300) throw new Error(`reconfigure refused: ${status}`);
  return body;
}

async function window(datasets, { expected }) {
  say('unpublish');
  const [unpublished] = await api('POST', `${BASE}/unpublish`);
  say('  ', unpublished);
  await waitFor('the space to unpublish', (live) => !live.published);

  const live = await deployment();
  await reconfigure(live.parameters, datasets);
  await waitFor('the workloads to come back', (live) => live.status === 'running', { tries: 60, every: 15000 });

  await dnsReady();
  const [published] = await api('POST', `${BASE}/publish`);
  say('publish', published);
  await waitFor('the space to publish', (live) => live.published);

  const { moved } = await checkEvidence(expected);
  const final = await deployment();
  const host = (final.endpoints || []).map((e) => e.host).find(Boolean);
  if (host) await checkLiveEvidence(host, expected);
  return { moved, final };
}

(async () => {
  const live = await deployment();
  say(`${live.appTitle} ${live.appVersion} — status=${live.status} published=${live.published}`);
  say('mounted now:', JSON.stringify(live.datasets.map((d) => d.datasetName)));

  const state = fs.existsSync(STATE) && rollback
    ? JSON.parse(fs.readFileSync(STATE, 'utf8'))
    : record(live);

  const target = rollback
    ? state.datasets
    : WANTED.map((datasetName) => ({ slot: SLOT, datasetName }));

  say(rollback ? 'rolling back to:' : 'mounting:', JSON.stringify(target.map((d) => d.datasetName)));
  say('parameters: taken from the live deployment verbatim,', Object.keys(live.parameters).length, 'of them');

  if (!rollback) {
    say('checking every card is published, gated and bindable before anything goes down');
    if (!(await checkCards(WANTED, { organizationId: ORG, appName: live.appName }))) {
      throw new Error('a card is not publishable into this deployment — nothing was touched');
    }
  }

  if (!apply && !rollback) {
    say('DRY RUN. Would send:');
    console.log(JSON.stringify({ parameters: live.parameters, datasets: target }, null, 1));
    say('re-run with --apply --i-have-a-go once the freeze is lifted for this window');
    return;
  }
  if (!authorised) throw new Error('refusing to move a frozen stand without --i-have-a-go');

  const { moved, final } = await window(target, { expected: state.expectedEvidenceDigest });

  say('');
  say(`status=${final.status} published=${final.published}`);
  say('mounted:', JSON.stringify((final.datasets || []).map((d) => d.datasetName)));
  if (moved) {
    say('THE DIGEST MOVED. The gate will refuse this deployment. Roll back now:');
    say(`  node ${path.basename(__filename)} --rollback --i-have-a-go`);
    process.exit(1);
  }
  say('');
  say('Now check the gate admitted it, on the marketplace host — the verdict log is the');
  say('only thing that says the clinic is actually being served:');
  say("  docker exec marketplace-postgres-1 psql -U marketplace -d swarm_marketplace -c \\");
  say('    "select l.name, e.verdict, e.reason, e.\\"occurredAt\\" from gatekeeper_access_events e');
  say('     join dataset_listings l on l.id = e.\\"datasetListingId\\"');
  say(`     where e.\\"deploymentId\\" = '${DEPLOYMENT}' order by e.\\"occurredAt\\" desc limit 20"`);
})().catch((error) => {
  console.log('FAILED', error.message);
  process.exit(1);
});
