#!/usr/bin/env node
// A data owner withdraws access to its own card, and puts it back — performed by
// the owner, on the owner's own account.
//
//   MP_BASE=https://marketplace.demo.superprotocol.dev \
//   OWNER=harbor-point-admin@local.test PASS_FILE=./harbor-point-admin.password \
//   DATASET=harborpoint-release GRANTEE=big-pharma \
//     node withdraw-grant.js                 # show the allow-lists, change nothing
//     node withdraw-grant.js --revoke        # drop the grantee, record what was there
//     node withdraw-grant.js --restore       # put back exactly what was recorded
//
// WHY IT IS A SCRIPT AND NOT A CLICK. It will be a click on the day somebody
// demonstrates it — `PATCH /api/v1/organizations/:org/datasets/:id/access` is the access
// dialog's single Save. This exists so the beat can be rehearsed, so the state
// before the revocation is RECORDED rather than remembered, and so the restore
// puts back the list that was there instead of the list somebody retyped.
//
// What the endpoint does, which is the part worth knowing before running it:
// it replaces BOTH allow-lists in one call, takes effect without republication,
// and then pushes a new admitted-clients document to the gate rather than
// waiting for the periodic sweep — "revoking a grant is the one moment an owner
// is watching for something to stop". So the revocation lands within seconds,
// and a `denied` verdict on the clinic's own port is what proves it landed.
const fs = require('node:fs');
const path = require('node:path');

const BASE = (process.env.MP_BASE || 'https://marketplace.demo.superprotocol.dev').replace(/\/$/, '');
const OWNER = required('OWNER');
const DATASET = required('DATASET');
const GRANTEE = process.env.GRANTEE || 'big-pharma';
const STATE = process.env.STATEFILE || path.join(__dirname, `.grant-${DATASET}.json`);
const revoke = process.argv.includes('--revoke');
const restore = process.argv.includes('--restore');

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`set ${name}`);
  return value;
}

const say = (...parts) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...parts);

async function signIn() {
  const response = await fetch(`${BASE}/api/auth/sign-in/email`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Origin: BASE },
    body: JSON.stringify({ email: OWNER, password: fs.readFileSync(required('PASS_FILE'), 'utf8').trim() }),
  });
  const payload = await response.json().catch(() => null);
  if (!payload || !payload.token) throw new Error(`sign-in ${response.status}`);
  return payload.token;
}

(async () => {
  const token = await signIn();
  const api = async (method, route, body) => {
    const response = await fetch(`${BASE}${route}`, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const text = await response.text();
    return [response.status, text ? JSON.parse(text) : null];
  };

  const [, organizations] = await api('GET', '/api/v1/organizations');
  // The clinic, not the persona's own personal workspace: a sign-up creates one
  // of those too, and it publishes nothing.
  const mine = (organizations.items || organizations || []).filter((o) => o.role === 'admin' || o.role === 'owner');
  let owner = null;
  let listing = null;
  for (const organization of mine) {
    const [status, datasets] = await api('GET', `/api/v1/organizations/${organization.id}/datasets`);
    if (status >= 300) continue;
    const found = (datasets || []).find((d) => d.name === DATASET);
    if (found) { owner = organization; listing = found; break; }
  }
  if (!listing) throw new Error(`${OWNER} publishes no dataset called ${DATASET}`);
  say(`${owner.slug} publishes ${listing.name} (${listing.id})`);

  const route = `/api/v1/organizations/${owner.id}/datasets/${listing.id}/access`;
  const [, access] = await api('GET', route);
  const organizationSlugs = (access.organizations || []).map((o) => o.slug);
  const appNames = (access.apps || []).map((a) => a.name);
  say(`  organisations ${JSON.stringify(organizationSlugs)}`);
  say(`  applications  ${JSON.stringify(appNames)}`);

  if (!revoke && !restore) {
    say(GRANTEE && organizationSlugs.includes(GRANTEE)
      ? `${GRANTEE} currently holds a grant — --revoke would drop it`
      : `${GRANTEE} holds no grant here`);
    return;
  }

  if (restore) {
    if (!fs.existsSync(STATE)) {
      throw new Error(
        `no recorded state at ${path.basename(STATE)} — there is nothing to put back. `
          + 'A restore has to restore something that was written down.',
      );
    }
    const saved = JSON.parse(fs.readFileSync(STATE, 'utf8'));
    if (saved.dataset !== DATASET) throw new Error(`${path.basename(STATE)} records ${saved.dataset}`);
    const [status, updated] = await api('PATCH', route, {
      organizationIds: saved.organizationIds,
      appListingIds: saved.appListingIds,
    });
    say(`restore ${status}`);
    if (status >= 300) throw new Error(`the restore was refused: ${JSON.stringify(updated).slice(0, 300)}`);
    say(`  organisations ${JSON.stringify((updated.organizations || []).map((o) => o.slug))}`);
    say(`  applications  ${JSON.stringify((updated.apps || []).map((a) => a.name))}`);
    say('the grant is back, and the gate was handed a new document in the same call');
    return;
  }

  const target = (access.organizations || []).find((o) => o.slug === GRANTEE);
  if (!target) {
    say(`${GRANTEE} holds no grant on ${DATASET} — nothing to revoke`);
    return;
  }
  // Written BEFORE the call, and it is the whole list rather than the one entry:
  // the endpoint replaces both allow-lists, so a restore has to know all of it.
  fs.writeFileSync(
    STATE,
    JSON.stringify({
      recordedAt: new Date().toISOString(),
      dataset: DATASET,
      organizationIds: (access.organizations || []).map((o) => o.id),
      appListingIds: (access.apps || []).map((a) => a.id),
      organizationSlugs,
      appNames,
    }, null, 1),
    { mode: 0o600 },
  );
  say(`recorded the current allow-lists in ${path.basename(STATE)}`);

  const [status, updated] = await api('PATCH', route, {
    organizationIds: (access.organizations || []).filter((o) => o.slug !== GRANTEE).map((o) => o.id),
    appListingIds: (access.apps || []).map((a) => a.id),
  });
  say(`revoke ${GRANTEE}: ${status}`);
  if (status >= 300) throw new Error(`the revocation was refused: ${JSON.stringify(updated).slice(0, 300)}`);
  say(`  organisations ${JSON.stringify((updated.organizations || []).map((o) => o.slug))}`);
  say(`  applications  ${JSON.stringify((updated.apps || []).map((a) => a.name))}`);
  say('');
  say(`${owner.slug} has withdrawn ${GRANTEE}'s access to ${DATASET}. The gate was handed a new`);
  say('admitted-clients document in the same call, so the next read is refused rather than');
  say('the one after a cache window. Put it back with --restore.');
})().catch((error) => {
  console.log('FAILED', error.message);
  process.exit(1);
});
