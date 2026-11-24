#!/usr/bin/env node
// Create the researcher tenant, by hand, mirroring what the identity bootstrap
// would have created if the listing could declare a fourth organisation.
//
//   KC_BASE=https://<stand>/auth KC_REALM=master KC_PASS_FILE=./stand.internalSecret \
//   APP_HOST=<stand host> RESEARCHER_PASS_FILE=./researcher.password \
//     node researcher-tenant.js
//   … node researcher-tenant.js --check      # read-only: report, change nothing
//
// WHY BY HAND, which is the part that needs defending.
//
// The Conversational Analyst listing carries exactly THREE organisation slots.
// `organizations` is a fixed three-entry list patched by numeric index, and the
// listing says why in its own comment: "A definition cannot append to a list —
// the specification's patch paths are dot-notation with numeric indices." A
// fourth needs a new listing version, and a reconfigure re-renders the version
// the deployment was CREATED from — so a new version reaches only new
// deployments, and re-creating this stand is what its freeze forbids.
//
// What makes the by-hand route honest rather than a shortcut: **the tenant list
// is not in the signed snapshot.** Every Secret in the published evidence
// carries metadata only — no `data`, on any of them, including
// `analyst-keycloak-bootstrap-env`, which is where the three organisations ride.
// So the three tenants the stand shows today are not attested either, and a
// fourth created here is no less attested than they are. Anything written about
// this must say that plainly rather than implying the snapshot vouches for the
// tenant boundary; what the snapshot vouches for is the code, and what enforces
// the boundary is the running Keycloak.
//
// The bootstrap Job only ADDS and never prunes — it POSTs each listed
// organisation and deletes none — so this organisation survives every
// re-render. It does not survive a re-created deployment, which is why this
// script exists and why `restore-stand.js` calls it.
//
// Mirrors `charts/conversational-analyst-identity/templates/bootstrap-job.yaml`
// step for step: the organisation with one domain under the stand's own host
// (Keycloak refuses an organisation with no domain and answers 400 without
// explaining), the `/org/{orgId}` group with its `admins` and `members`
// children, and a member. One deliberate difference: the bootstrap adds the
// INSTALLATION ADMINISTRATOR to the first organisation only, so a fourth
// created to match would be empty and nobody could sign into it. This gives the
// researcher its OWN user instead — which is also the correct boundary: the
// researcher must not be a member of any clinic organisation, and this script
// refuses to finish if it is.
const fs = require('node:fs');

const BASE = required('KC_BASE').replace(/\/$/, '');
const REALM = process.env.KC_REALM || 'master';
const APP_HOST = required('APP_HOST');
const ORG_NAME = process.env.ORG_NAME || 'Clinical Research Network';
const ORG_ALIAS = process.env.ORG_ALIAS || 'research-network';
const RESEARCHER = process.env.RESEARCHER_USER || `${ORG_ALIAS}-admin@local.test`;
const CHECK_ONLY = process.argv.includes('--check');

/** Organisations the researcher must NOT belong to. Named rather than derived:
 *  "every organisation that is not ours" would quietly stop checking the day
 *  somebody adds a fourth clinic. */
const CLINIC_ALIASES = (process.env.CLINIC_ALIASES || 'cypress-bay,latica,pharma').split(',');

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
  if (!response.ok || !payload?.access_token) throw new Error(`token ${response.status}`);
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
  if (!response.ok) {
    throw new Error(`${method} ${path} ${response.status} ${(await response.text()).slice(0, 200)}`);
  }
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

/** Create unless it is already there, and say which happened. Every step here is
 *  re-runnable: this script is called by a restore, which may be run twice. */
async function ensure(what, find, create) {
  const existing = await find();
  if (existing) {
    say(`  ${what}: already there`);
    return existing;
  }
  if (CHECK_ONLY) {
    say(`  ${what}: MISSING`);
    return null;
  }
  await create();
  const made = await find();
  if (!made) throw new Error(`${what} was not created`);
  say(`  ${what}: created`);
  return made;
}

(async () => {
  const access = await token();
  say(`${CHECK_ONLY ? 'checking' : 'ensuring'} the researcher tenant: ${ORG_NAME} (${ORG_ALIAS})`);

  const organizations = await kc('GET', 'organizations', access);
  const org = await ensure(
    `organisation ${ORG_ALIAS}`,
    async () => (await kc('GET', 'organizations', access)).find((o) => o.alias === ORG_ALIAS),
    () =>
      kc('POST', 'organizations', access, {
        name: ORG_NAME,
        alias: ORG_ALIAS,
        enabled: true,
        // One domain, under this deployment's own host, so four of them cannot
        // collide. Keycloak refuses an organisation with no domain at all.
        domains: [{ name: `${ORG_ALIAS}.${APP_HOST}`, verified: true }],
      }),
  );
  if (!org) {
    say('nothing else can be checked without the organisation');
    process.exitCode = 1;
    return;
  }

  const orgRoot = (await kc('GET', 'groups?search=org', access)).find((g) => g.path === '/org');
  if (!orgRoot) throw new Error('the realm has no /org group; this is not a stand built by the identity chart');

  const node = await ensure(
    `group /org/${org.id}`,
    async () =>
      (await kc('GET', `groups/${orgRoot.id}/children?search=${org.id}`, access)).find((g) => g.name === org.id),
    () => kc('POST', `groups/${orgRoot.id}/children`, access, { name: org.id }),
  );

  const leaves = {};
  for (const leaf of ['admins', 'members']) {
    // `/org/{orgId}/admins` IS org-admin for that organisation and
    // `/org/{orgId}/members` IS org-member; no role object is bound to either.
    leaves[leaf] = node
      ? await ensure(
          `group /org/${org.id}/${leaf}`,
          async () =>
            (await kc('GET', `groups/${node.id}/children?search=${leaf}`, access)).find((g) => g.name === leaf),
          () => kc('POST', `groups/${node.id}/children`, access, { name: leaf }),
        )
      : null;
  }

  const user = await ensure(
    `user ${RESEARCHER}`,
    async () =>
      (await kc('GET', `users?username=${encodeURIComponent(RESEARCHER)}&exact=true`, access))[0],
    () =>
      kc('POST', 'users', access, {
        username: RESEARCHER,
        email: RESEARCHER,
        emailVerified: true,
        enabled: true,
      }),
  );

  if (user && !CHECK_ONLY && process.env.RESEARCHER_PASS_FILE) {
    await kc('PUT', `users/${user.id}/reset-password`, access, {
      type: 'password',
      value: fs.readFileSync(process.env.RESEARCHER_PASS_FILE, 'utf8').trim(),
      temporary: false,
    });
    say('  password: set');
  }

  if (user) {
    await ensure(
      `${RESEARCHER} is a member of ${ORG_ALIAS}`,
      async () => (await kc('GET', `organizations/${org.id}/members`, access)).find((m) => m.id === user.id),
      // The body is the user id as a bare JSON string, which is what this
      // endpoint takes; an object is accepted and does nothing.
      () => kc('POST', `organizations/${org.id}/members`, access, user.id),
    );
    if (leaves.admins) {
      await ensure(
        `${RESEARCHER} is in /org/${org.id}/admins`,
        async () => (await kc('GET', `users/${user.id}/groups`, access)).find((g) => g.id === leaves.admins.id),
        () => kc('PUT', `users/${user.id}/groups/${leaves.admins.id}`, access),
      );
    }
  }

  // The boundary, asserted rather than assumed. A researcher who is also a
  // member of a clinic organisation is not a researcher, and this is the one
  // mistake that would make every claim about the fourth tenant false.
  say('the boundary:');
  let leaked = [];
  for (const alias of CLINIC_ALIASES) {
    const clinic = organizations.find((o) => o.alias === alias);
    if (!clinic) { say(`  ${alias}: no such organisation, skipped`); continue; }
    const members = await kc('GET', `organizations/${clinic.id}/members`, access);
    const present = user && members.some((m) => m.id === user.id);
    say(`  ${alias}: ${members.length} member(s), researcher present = ${present}`);
    if (present) leaked.push(alias);
  }
  if (leaked.length > 0) {
    say(`!! ${RESEARCHER} is a member of ${leaked.join(', ')} — it must belong to ${ORG_ALIAS} and nothing else`);
    process.exitCode = 1;
    return;
  }

  const mine = await kc('GET', `organizations/${org.id}/members`, access);
  say(`${ORG_ALIAS} (${org.id}) has ${mine.length} member(s): ${mine.map((m) => m.username).join(', ')}`);
  say(CHECK_ONLY ? 'checked' : 'the researcher tenant is in place');
})().catch((error) => {
  console.log('FAILED', error.message);
  process.exit(1);
});
