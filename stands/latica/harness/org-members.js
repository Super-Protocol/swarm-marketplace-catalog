#!/usr/bin/env node
// Save, trim and restore a Keycloak organisation's membership.
//
//   KC_BASE=…/auth KC_REALM=master KC_PASS_FILE=./stand.internalSecret \
//     ORG_ALIAS=cypress-bay node org-members.js --save ./members.json
//   … node org-members.js --trim ./members.json --keep somebody@example.test
//   … node org-members.js --restore ./members.json
//
// WHY THIS EXISTS, because it looks like a strange thing to want.
//
// `ground.py`'s `identity()` picks the subject it acts as from
// `organizations/<tenant>/members[0]` — the FIRST member, whatever order
// Keycloak returns them in. Its own comment says why that was thought safe:
// "The administrator the bootstrap created is its only member at this point."
// That holds the moment a deployment is created and stops holding as soon as
// anybody else joins the tenant organisation.
//
// `main()` then calls `ensure_model` before `ensure_sources`, and every
// model-registry operation on the agent is `@RequirePermission('installation.
// llm-models.*')` — an INSTALLATION permission the bootstrap administrator has
// and an ordinary member of the organisation does not. So once a second member
// sorts ahead of the administrator, the Job's very first registry call is
// refused, on every restart, until `backoffLimit: 10` is spent — and it never
// reaches `ensure_sources`. The symptom is a deployment that attests perfectly
// and has registered none of the datasets it mounted.
//
// On the Latica stand that is exactly what happened: `cypress-bay` gained
// `cypress-bay-admin@local.test` after the deployment was created, it sorts
// before `gate-rehearsal@local.test`, and two windows' worth of grounding Jobs
// failed identically.
//
// A reconfigure re-renders the version the deployment was created from, so the
// chart cannot be fixed in place for an existing stand. Trimming the
// organisation to the administrator for the length of a window is the way to let
// the declared path run, and this script is here so that doing it is recorded,
// repeatable and reversible rather than a sequence of curl commands somebody
// half-remembers.
//
// The durable fix belongs in `ground.py`: select the subject by the bootstrap's
// `ADMIN_USERNAME` and fail loudly if that user is not a member. Until a stand is
// on a version carrying that, **a window needs a trim before it and a restore
// after it**, because Keycloak's order puts the administrator second again as
// soon as the others are back.
const fs = require('node:fs');

const BASE = required('KC_BASE').replace(/\/$/, '');
const REALM = process.env.KC_REALM || 'master';
const ALIAS = required('ORG_ALIAS');

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`set ${name}`);
  return value;
}

const say = (...parts) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...parts);

async function token() {
  const body = new URLSearchParams({
    client_id: 'admin-cli',
    grant_type: 'password',
    username: process.env.KC_ADMIN_USER || 'admin',
    password: fs.readFileSync(required('KC_PASS_FILE'), 'utf8').trim(),
  });
  const response = await fetch(`${BASE}/realms/${REALM}/protocol/openid-connect/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload || !payload.access_token) throw new Error(`token ${response.status}`);
  return payload.access_token;
}

async function kc(method, path, access, body) {
  const response = await fetch(`${BASE}/admin/realms/${REALM}/${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${access}`,
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) throw new Error(`${method} ${path} ${response.status} ${(await response.text()).slice(0, 200)}`);
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

async function organization(access) {
  const all = await kc('GET', 'organizations', access);
  const found = all.find((o) => o.alias === ALIAS);
  if (!found) throw new Error(`no organization with alias ${ALIAS}; realm has ${all.map((o) => o.alias).join(', ')}`);
  return found;
}

(async () => {
  const mode = process.argv.find((a) => ['--save', '--trim', '--restore'].includes(a));
  const file = process.argv[process.argv.indexOf(mode) + 1];
  if (!mode || !file || file.startsWith('--')) {
    throw new Error('usage: --save|--trim|--restore <file> [--keep <username>]');
  }
  const keep = process.argv.includes('--keep') ? process.argv[process.argv.indexOf('--keep') + 1] : null;

  const access = await token();
  const org = await organization(access);
  const members = await kc('GET', `organizations/${org.id}/members`, access);
  say(`${ALIAS} (${org.id}) has ${members.length} member(s), in Keycloak's own order:`);
  for (const [index, m] of members.entries()) say(`  [${index}] ${m.username}  ${m.id}`);

  if (mode === '--save') {
    // Group memberships go in the record too. They are a separate thing from
    // organisation membership and this script does not touch them — but a
    // restore that could not say what they were would be a restore nobody can
    // check, so they are written down.
    const record = { savedAt: new Date().toISOString(), realm: REALM, alias: ALIAS, organizationId: org.id, members: [] };
    for (const m of members) {
      const groups = await kc('GET', `users/${m.id}/groups`, access).catch(() => []);
      record.members.push({ id: m.id, username: m.username, email: m.email, groups: groups.map((g) => g.path) });
    }
    fs.writeFileSync(file, JSON.stringify(record, null, 1), { mode: 0o600 });
    say(`saved ${record.members.length} member(s) and their groups to ${file}`);
    return;
  }

  if (mode === '--trim') {
    if (!keep) throw new Error('--trim needs --keep <username>: the member to leave behind');
    if (!fs.existsSync(file)) throw new Error(`${file} does not exist — run --save first, or there is no way back`);
    const saved = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (saved.organizationId !== org.id) throw new Error(`${file} records a different organisation`);
    const kept = members.find((m) => m.username === keep);
    if (!kept) throw new Error(`${keep} is not a member of ${ALIAS}; trimming would leave it with nobody`);
    for (const m of members) {
      if (m.username === keep) continue;
      await kc('DELETE', `organizations/${org.id}/members/${m.id}`, access);
      say(`  removed ${m.username}`);
    }
    const left = await kc('GET', `organizations/${org.id}/members`, access);
    say(`${left.length} member(s) left: ${left.map((m) => m.username).join(', ')}`);
    if (left.length !== 1 || left[0].username !== keep) throw new Error('the trim did not leave exactly the kept member');
    return;
  }

  // --restore
  const saved = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (saved.organizationId !== org.id) throw new Error(`${file} records a different organisation`);
  const present = new Set(members.map((m) => m.id));
  for (const m of saved.members) {
    if (present.has(m.id)) { say(`  ${m.username} is already a member`); continue; }
    // The body is the user id as a bare JSON string, which is what this endpoint
    // takes — an object is accepted and does nothing.
    await kc('POST', `organizations/${org.id}/members`, access, m.id);
    say(`  re-added ${m.username}`);
  }
  const now = await kc('GET', `organizations/${org.id}/members`, access);
  const missing = saved.members.filter((m) => !now.some((x) => x.id === m.id));
  say(`${now.length} member(s) now: ${now.map((m) => m.username).join(', ')}`);
  if (missing.length > 0) throw new Error(`not restored: ${missing.map((m) => m.username).join(', ')}`);
  say('every saved member is back');
})().catch((error) => {
  console.log('FAILED', error.message);
  process.exit(1);
});
