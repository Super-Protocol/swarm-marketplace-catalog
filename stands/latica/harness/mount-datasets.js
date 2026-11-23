#!/usr/bin/env node
// Mount a different set of dataset cards on the analyst stand, in one
// `unpublish → reconfigure → publish` window, and prove afterwards that the
// stand came back to the state it was in.
//
//   MP_ORG=… MP_DEPLOYMENT=… node mount-datasets.js                       # dry run, the default
//   MP_ORG=… MP_DEPLOYMENT=… node mount-datasets.js --verify               # the post-window checks, alone
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
// grounding Job is not collected at all. After the publish the script therefore
// asserts two things and exits non-zero on either: that the cloud's measured
// digest still equals what the listing version declares, which is the comparison
// `clientsFor` makes and therefore whether the gate admits this deployment at
// all; and that the measured digest is unchanged from before the window, which
// is whether this window moved it. Both come from `GET …/evidence`. Neither is
// `expectedEvidenceDigest` off the deployment — that is `renderDigest` under its
// former name, computed from the manifests before deploying, and a render digest
// can never equal a collected one.
//
// Every published hostname is then fetched directly, because the marketplace
// reads the cloud's API and the gate reads the hostname: a stand serving a stale
// or missing bundle is dark to the gate while the marketplace still looks right.
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
const verify = process.argv.includes('--verify');
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

/** Everything needed to put the deployment back, and the digest the window has to
 *  leave alone.
 *
 *  The baseline is the digest the CLOUD MEASURED, read from `GET …/evidence`
 *  before the window. Not `expectedEvidenceDigest` off the deployment: that field
 *  is `renderDigest` under its former name (`deployments.controller.ts:61`), a
 *  number this marketplace computed from the manifests before deploying, and a
 *  render digest can never equal a collected one — a cluster defaults fields no
 *  template writes. Comparing against it reports a mismatch on every healthy
 *  deployment, which in this script would have ordered a rollback of a stand
 *  that was fine.
 *
 *  `written` is false on a dry run: the recovery file is the record of a window
 *  that actually happened, and a dry run that overwrote it would quietly replace
 *  the pre-window state with the post-window one. */
async function baseline(live, { written }) {
  const [, evidence] = await api('GET', `${BASE}/evidence`);
  const state = {
    recordedAt: new Date().toISOString(),
    deploymentId: live.id,
    appVersion: live.appVersion,
    published: live.published,
    // What the gate holds this deployment to, and what the cloud actually
    // measured. Both, because the pair is the verdict: `matchesExpected` says
    // the gate will admit it at all, and `digest` unchanged says this window did
    // not move it.
    declaredDigest: (evidence && evidence.expectedDigest) ?? null,
    measuredDigest: (evidence && evidence.digest) ?? null,
    matchesExpected: (evidence && evidence.matchesExpected) ?? null,
    renderDigest: live.renderDigest ?? null,
    endpoints: live.endpoints ?? [],
    parameters: live.parameters,
    datasets: live.datasets,
  };
  say(`before: measured ${state.measuredDigest} · declared ${state.declaredDigest} · matches ${state.matchesExpected}`);
  if (state.matchesExpected !== true) {
    say('  !! this deployment does not match its declared digest BEFORE the window.');
    say('     The gate is already refusing it, and the window is not what to fix first.');
  }
  if (written) {
    fs.writeFileSync(STATE, JSON.stringify(state, null, 1), { mode: 0o600 });
    say(`recovery state written to ${path.basename(STATE)}`);
  }
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

/** What the cloud measured after the window, against the two things that matter.
 *
 *  Two conditions, not one. `matchesExpected` is whether the gate will admit this
 *  deployment at all — it is the measured digest against the one the listing
 *  version declares, which is exactly the comparison `clientsFor` makes. And the
 *  measured digest unchanged from the baseline is whether THIS window moved it.
 *  A mounted dataset should move neither: the two collected sidecar objects that
 *  change carry `swarm.io/exclude-evidence-fields` for the fields that change,
 *  and the grounding Job is not collected. */
async function checkEvidence(state) {
  for (let attempt = 1; attempt <= 25; attempt += 1) {
    const [, evidence] = await api('GET', `${BASE}/evidence`);
    if (evidence && evidence.digest) {
      const resources = (evidence.snapshot && evidence.snapshot.resources) || [];
      const kinds = {};
      for (const resource of resources) kinds[resource.kind] = (kinds[resource.kind] || 0) + 1;
      say('  measured       ', evidence.digest);
      say('  declared       ', evidence.expectedDigest);
      say('  matchesExpected', evidence.matchesExpected);
      say('  resources      ', resources.length, JSON.stringify(kinds));

      const problems = [];
      if (evidence.matchesExpected !== true) {
        problems.push(
          `the gate will refuse this deployment: measured ${evidence.digest} is not the declared `
            + `${evidence.expectedDigest}`,
        );
      }
      if (state.measuredDigest && digestHex(evidence.digest) !== digestHex(state.measuredDigest)) {
        problems.push(`this window moved the measured digest — it was ${state.measuredDigest}`);
      }
      return { evidence, problems };
    }
    say('  no evidence published yet');
    await sleep(20000);
  }
  throw new Error('no evidence inside the window');
}

/** The same 32 bytes, whichever of the two spellings carried them.
 *
 *  The marketplace reports a digest as bare lowercase hex. The published bundle
 *  reports it as `sha256/<base64url>` — the multihash-ish form the attestation
 *  code uses. They are the same value, and a string comparison between them is
 *  false every time: `d391cb85…` against
 *  `sha256/05HLhdgiR5fOaf50fBHQhRUF61GdXh4t9NL602zAvzo`. Both normalise here, so
 *  the comparison is about the digest rather than about who formatted it. */
function digestHex(digest) {
  if (!digest) return null;
  const value = String(digest);
  const encoded = value.includes('/') ? value.slice(value.indexOf('/') + 1) : value;
  if (/^[0-9a-f]{64}$/i.test(encoded)) return encoded.toLowerCase();
  const padded = encoded.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (encoded.length % 4)) % 4);
  const bytes = Buffer.from(padded, 'base64');
  if (bytes.length !== 32) throw new Error(`cannot read ${value} as a sha-256 digest`);
  return bytes.toString('hex');
}

/** The same bundle the gate fetches, from the hostname a consumer uses.
 *
 *  Worth checking separately from the route above: the marketplace reads the
 *  cloud's API, the gate reads this, and a stand whose hostname serves a stale or
 *  missing bundle is dark to the gate while the marketplace still looks right. */
async function checkLiveEvidence(host, expected) {
  let bundle;
  try {
    const response = await fetch(`https://${host}/.well-known/swarm-evidence`, {
      signal: AbortSignal.timeout(20000),
    });
    if (!response.ok) return [`/.well-known/swarm-evidence on ${host} answered ${response.status}`];
    bundle = await response.json();
  } catch (error) {
    return [`/.well-known/swarm-evidence on ${host} could not be read: ${error.message}`];
  }

  const payload = JSON.parse(
    Buffer.from(String(bundle.jws).split('.')[1].replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString('utf8'),
  );
  const served = digestHex(payload.evidenceDigest);
  const wanted = digestHex(expected);
  say(`  live bundle on ${host}: ${payload.evidenceDigest}`);
  if (wanted && served !== wanted) {
    return [`${host} serves ${served}, but the cloud measured ${wanted}`];
  }
  return [];
}

async function reconfigure(parameters, datasets) {
  const [status, body] = await api('POST', `${BASE}/reconfigure`, { parameters, datasets });
  say('reconfigure', status, JSON.stringify(body && { status: body.status, message: body.statusMessage }));
  if (status >= 300) throw new Error(`reconfigure refused: ${status}`);
  return body;
}

async function window(datasets, state) {
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

  const { evidence, problems } = await checkEvidence(state);
  const final = await deployment();

  // Every published hostname, not the first: they serve the same bundle, and one
  // of them not serving it is the failure this check exists to catch.
  const hosts = (final.endpoints || []).map((endpoint) => endpoint.host).filter(Boolean);
  if (hosts.length === 0) problems.push('the deployment publishes no hostname');
  for (const host of hosts) {
    problems.push(...(await checkLiveEvidence(host, evidence.digest)));
  }

  return { problems, final };
}

/** The post-window checks, run on their own.
 *
 *  Separate from the window so the same assertions can be made at any time
 *  without taking anything down — before a window to see the stand is healthy to
 *  begin with, and after one to see it still is. Read-only. */
async function verifyOnly(saved) {
  const { evidence, problems } = await checkEvidence(saved ?? {});
  const live = await deployment();
  const hosts = (live.endpoints || []).map((endpoint) => endpoint.host).filter(Boolean);
  if (hosts.length === 0) problems.push('the deployment publishes no hostname');
  for (const host of hosts) problems.push(...(await checkLiveEvidence(host, evidence.digest)));

  say('');
  if (problems.length > 0) {
    for (const problem of problems) say(`  !! ${problem}`);
    return 1;
  }
  say(
    saved && saved.measuredDigest
      ? 'the gate will admit this deployment, and the digest is unchanged from the recorded baseline'
      : 'the gate will admit this deployment; no baseline recorded, so nothing to compare it against',
  );
  return 0;
}

(async () => {
  const live = await deployment();
  say(`${live.appTitle} ${live.appVersion} — status=${live.status} published=${live.published}`);
  say('mounted now:', JSON.stringify(live.datasets.map((d) => d.datasetName)));

  const saved = fs.existsSync(STATE) ? JSON.parse(fs.readFileSync(STATE, 'utf8')) : null;

  if (verify) {
    process.exit(await verifyOnly(saved));
  }

  // A rollback is only a rollback if something recorded where to go back TO.
  // Without the file this would have read the CURRENT state as the state to
  // restore and reported success for putting things back exactly as they are.
  if (rollback) {
    if (!saved) {
      throw new Error(
        `no recovery state at ${path.basename(STATE)} — there is nothing recorded to roll back to, `
          + 'and restoring the current state is not a rollback. Mount the previous set by name with '
          + 'MP_DATASETS=… --apply if you know what it was.',
      );
    }
    if (saved.deploymentId !== DEPLOYMENT) {
      throw new Error(`${path.basename(STATE)} records deployment ${saved.deploymentId}, not this one`);
    }
  }

  let state;
  if (rollback) {
    state = saved;
    say(`rolling back to the state recorded at ${state.recordedAt}`);
  } else if (apply && saved && !saved.completedAt) {
    // An unfinished window, being retried. Keep the original baseline: the
    // digest and the dataset list to compare against are the ones from before
    // the first attempt, not from the half-finished state it left behind.
    state = saved;
    say(`reusing the baseline recorded at ${state.recordedAt} — a window was started and not finished`);
  } else {
    state = await baseline(live, { written: apply });
  }

  // A window run on a deployment that is ALREADY failing its digest check cannot
  // be judged: the post-window assertion would find a mismatch and blame the
  // window for one it inherited, and the rollback it ordered would restore a
  // state the gate was refusing anyway. Refused rather than warned about — the
  // whole point of the guard is that somebody reads it, and a warning in the
  // middle of a window's output is not read.
  //
  // `--rollback` is deliberately exempt: a stand in that state is exactly when
  // you want to put it back.
  if (apply && !rollback && state.matchesExpected !== true) {
    throw new Error(
      'this deployment does not match its declared digest BEFORE the window '
        + `(measured ${state.measuredDigest}, declared ${state.declaredDigest}) — the gate is `
        + 'already refusing it. Fix that first; a window run from here cannot be judged, and its '
        + 'rollback would restore a state the gate was refusing anyway.',
    );
  }

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
    say('DRY RUN — nothing was written, including the recovery file. Would send:');
    console.log(JSON.stringify({ parameters: live.parameters, datasets: target }, null, 1));
    say('re-run with --apply --i-have-a-go once the freeze is lifted for this window');
    return;
  }
  if (!authorised) throw new Error('refusing to move a frozen stand without --i-have-a-go');

  const { problems, final } = await window(target, state);

  say('');
  say(`status=${final.status} published=${final.published}`);
  say('mounted:', JSON.stringify((final.datasets || []).map((d) => d.datasetName)));
  if (problems.length > 0) {
    say('');
    for (const problem of problems) say(`  !! ${problem}`);
    say('');
    say('Roll back now rather than debugging a stand that is down:');
    say(`  node ${path.basename(__filename)} --rollback --i-have-a-go`);
    process.exit(1);
  }

  // Written only now: a baseline with no `completedAt` is an unfinished window,
  // and the next `--apply` reuses it rather than recording the broken state as
  // the thing to go back to.
  if (!rollback) {
    fs.writeFileSync(
      STATE,
      JSON.stringify({ ...state, completedAt: new Date().toISOString() }, null, 1),
      { mode: 0o600 },
    );
  }
  say('');
  say('Now check the gate admitted it, on the marketplace host — the verdict log is the');
  say('only thing that says the clinic is actually being served:');
  say("  docker exec marketplace-postgres-1 psql -U marketplace -d swarm_marketplace -c \\");
  say('    "select l.name, e.verdict, e.reason, e.\\"occurredAt\\" from gatekeeper_access_events e');
  say('     join dataset_listings l on l.id = e.\\"datasetListingId\\"');
  say(`     where e.\\"deploymentId\\" = '${DEPLOYMENT}' order by e.\\"occurredAt\\" desc limit 20"`);
})().catch(async (error) => {
  console.log('FAILED', error.message);

  // Where the stand actually is, and the one command that puts it back. A
  // failure in the middle of a window leaves somebody reading this output with
  // the stand down, and "FAILED <message>" alone does not say whether it is
  // published, whether a rollback is possible, or what to type. Best effort: if
  // even this read fails, say so rather than throwing a second error over the
  // first.
  try {
    const live = await deployment();
    console.log(
      `       stand: status=${live.status} published=${live.published} `
        + `mounted=${JSON.stringify((live.datasets || []).map((d) => d.datasetName))}`,
    );
    if (!live.published) {
      console.log('       the stand is NOT published — it is off the air until a publish succeeds.');
    }
  } catch (second) {
    console.log(`       could not read the deployment to report its state: ${second.message}`);
  }

  if (fs.existsSync(STATE)) {
    const saved = JSON.parse(fs.readFileSync(STATE, 'utf8'));
    console.log(
      `       recovery state from ${saved.recordedAt}`
        + `${saved.completedAt ? ' (a completed window)' : ' (a window that did not finish)'}:`
        + ` ${JSON.stringify(saved.datasets.map((d) => d.datasetName))}`,
    );
    console.log(`       put it back with:  node ${path.basename(__filename)} --rollback --i-have-a-go`);
  } else {
    console.log('       no recovery state on disk, so nothing was changed by this run.');
  }

  process.exit(1);
});
