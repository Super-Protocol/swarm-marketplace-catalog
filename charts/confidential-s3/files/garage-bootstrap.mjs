#!/usr/bin/env node
/**
 * Brings a fresh Garage to the state the gateway needs, and does nothing to one
 * that is already there.
 *
 * A Garage that has just started accepts no writes: it has no storage layout, and
 * the gateway's credential does not exist. Both are one-time cluster decisions
 * rather than configuration, so they cannot live in `garage.toml` — which is why
 * this exists at all.
 *
 * It runs as a Kubernetes Job in the marketplace deployment (the chart mounts it
 * from a ConfigMap) and as a step in `tests/images/run.sh`. A Job's
 * `spec.template` is immutable, so the platform deletes and re-creates it on every
 * reconfigure: **every step below has to be safe to repeat**, and each one is.
 *
 *   1. wait for the admin API;
 *   2. assign this node a role and apply the layout — skipped once any role exists;
 *   3. import the gateway's access key — a 409 means it is already imported, which
 *      is success, not a conflict to resolve;
 *   4. allow it to create buckets, which is how an internal bucket comes into
 *      being on the first request that touches it.
 *
 * The credential is *imported*, never generated here. Garage would hand back a
 * random pair, and a Job that mints a secret has nowhere to put it: a published
 * cluster space is frozen and the gateway reads the credential from a Secret that
 * was sealed before any of this ran. The chart derives both halves from one
 * generated parameter instead, so the two sides agree without anybody writing
 * anything down. Garage's format is not negotiable — `GK` plus 24 hex characters,
 * and 64 hex characters — and it rejects anything else with a 400.
 *
 * Environment:
 *   GARAGE_ADMIN_URL     http://<service>:3903
 *   GARAGE_ADMIN_TOKEN   the admin token from garage.toml
 *   ENGINE_ACCESS_KEY    GK + 24 hex
 *   ENGINE_SECRET_KEY    64 hex
 *   GARAGE_ZONE          layout zone name           (default: swarm)
 *   GARAGE_CAPACITY      layout capacity, in bytes  (default: 10 GiB)
 *   WAIT_TIMEOUT_SECONDS how long to wait for the admin API (default: 300)
 */

const adminUrl = (process.env.GARAGE_ADMIN_URL ?? 'http://127.0.0.1:3903').replace(/\/+$/, '');
const adminToken = required('GARAGE_ADMIN_TOKEN');
const accessKey = required('ENGINE_ACCESS_KEY');
const secretKey = required('ENGINE_SECRET_KEY');
const zone = process.env.GARAGE_ZONE?.trim() || 'swarm';
const capacity = Number(process.env.GARAGE_CAPACITY ?? 10 * 1024 ** 3);
const waitTimeoutMs = Number(process.env.WAIT_TIMEOUT_SECONDS ?? 300) * 1000;

function required(name) {
  const value = process.env[name]?.trim();
  if (!value) {
    console.error(`${name} is required`);
    process.exit(2);
  }
  return value;
}

function log(message) {
  console.log(`[garage-bootstrap] ${message}`);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function admin(method, path, body) {
  const response = await fetch(`${adminUrl}${path}`, {
    method,
    headers: {
      authorization: `Bearer ${adminToken}`,
      ...(body === undefined ? {} : { 'content-type': 'application/json' }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  let parsed;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = text;
  }
  return { status: response.status, ok: response.ok, body: parsed, text };
}

async function waitForAdminApi() {
  const deadline = Date.now() + waitTimeoutMs;
  for (;;) {
    try {
      const status = await admin('GET', '/v1/status');
      if (status.ok) return status.body;
    } catch {
      // Not up yet. A connection refused here is the normal first second.
    }
    if (Date.now() > deadline) {
      throw new Error(`the admin API at ${adminUrl} did not answer within ${waitTimeoutMs / 1000}s`);
    }
    await sleep(2000);
  }
}

async function ensureLayout() {
  const layout = await admin('GET', '/v1/layout');
  if (!layout.ok) throw new Error(`GET /v1/layout: ${layout.status} ${layout.text}`);

  if (layout.body.roles?.length) {
    log(`layout version ${layout.body.version} already has ${layout.body.roles.length} role(s)`);
    return;
  }

  const status = await admin('GET', '/v1/status');
  if (!status.ok) throw new Error(`GET /v1/status: ${status.status} ${status.text}`);
  const nodeId = status.body.node;

  const staged = await admin('POST', '/v1/layout', [{ id: nodeId, zone, capacity, tags: [] }]);
  if (!staged.ok) throw new Error(`POST /v1/layout: ${staged.status} ${staged.text}`);

  // Applying a version that is not exactly the next one is refused, which is what
  // makes "read the current version, add one" the only correct form here.
  const applied = await admin('POST', '/v1/layout/apply', { version: layout.body.version + 1 });
  if (!applied.ok) throw new Error(`POST /v1/layout/apply: ${applied.status} ${applied.text}`);
  log(`assigned ${nodeId.slice(0, 16)} to zone ${zone} with ${capacity} bytes`);
}

async function ensureKey() {
  const imported = await admin('POST', '/v1/key/import', {
    accessKeyId: accessKey,
    secretAccessKey: secretKey,
    name: 'gateway',
  });
  if (imported.ok) {
    log(`imported ${accessKey}`);
  } else if (imported.status === 409) {
    log(`${accessKey} is already imported`);
  } else {
    throw new Error(`POST /v1/key/import: ${imported.status} ${imported.text}`);
  }

  const allowed = await admin('POST', `/v1/key?id=${encodeURIComponent(accessKey)}`, {
    allow: { createBucket: true },
  });
  if (!allowed.ok) throw new Error(`POST /v1/key: ${allowed.status} ${allowed.text}`);
  log('the gateway may create buckets');
}

const status = await waitForAdminApi();
log(`garage ${status.garageVersion} answered`);
await ensureLayout();
await ensureKey();
log('done');
