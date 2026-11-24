// Finish a realm the bootstrap Job left half-built, through the realm's own
// admin API.
//
//   KC_BASE=https://<host>/auth KC_PASS_FILE=./<name>.internalSecret \
//   ADMIN_USERNAME=<the installation administrator> APP_HOST=<host> \
//   ORGANIZATIONS='Name|alias
//   Name|alias' node finish-bootstrap.js
//   … CHECK=1 node finish-bootstrap.js        # report, change nothing
//
// Mirrors `charts/conversational-analyst-identity/templates/bootstrap-job.yaml`
// from "realm administrator" onward: the two admin grants, the realm's
// organisations switch, the `/org` group tree, one organisation per line, and
// the administrator's membership of the FIRST one — which is the one the chart
// adds them to, and therefore the tenant the grounding Job will act in.
//
// Every step is a create-unless-present, because this runs on a realm that is
// partly built and may be run again.
const fs = require('node:fs');

const BASE = process.env.KC_BASE.replace(/\/$/, '');
const REALM = process.env.KC_REALM || 'master';
const ADMIN_USERNAME = process.env.ADMIN_USERNAME;
const APP_HOST = process.env.APP_HOST;
const ORGANIZATIONS = (process.env.ORGANIZATIONS || '')
  .split('\n').map((line) => line.trim()).filter(Boolean)
  .map((line) => { const i = line.indexOf('|'); return [line.slice(0, i), line.slice(i + 1)]; });
const CHECK = Boolean(process.env.CHECK);

const say = (...parts) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...parts);

async function token() {
  const body = new URLSearchParams({
    client_id: 'admin-cli', grant_type: 'password',
    username: process.env.KC_ADMIN_USER || 'admin',
    password: fs.readFileSync(process.env.KC_PASS_FILE, 'utf8').trim(),
  });
  const r = await fetch(`${BASE}/realms/${REALM}/protocol/openid-connect/token`, {
    method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body,
  });
  const p = await r.json().catch(() => null);
  if (!p || !p.access_token) throw new Error(`token ${r.status}`);
  return p.access_token;
}

(async () => {
  let access = await token();
  const api = async (method, path, payload) => {
    const r = await fetch(`${BASE}/admin/realms/${REALM}/${path}`, {
      method,
      headers: { Authorization: `Bearer ${access}`, ...(payload === undefined ? {} : { 'Content-Type': 'application/json' }) },
      ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
    });
    const text = await r.text();
    // The admin token is short-lived and this makes a few dozen calls; one
    // refresh and one retry rather than a failure somebody has to re-run.
    if (r.status === 401) { access = await token(); return api(method, path, payload); }
    if (!r.ok && r.status !== 409) throw new Error(`${method} ${path} ${r.status} ${text.slice(0, 200)}`);
    return text ? JSON.parse(text) : null;
  };
  const ensure = async (what, find, create) => {
    const existing = await find();
    if (existing) { say(`  ${what}: already there`); return existing; }
    if (CHECK) { say(`  ${what}: MISSING`); return null; }
    await create();
    const made = await find();
    if (!made) throw new Error(`${what} was not created`);
    say(`  ${what}: created`);
    return made;
  };

  const user = (await api('GET', `users?username=${encodeURIComponent(ADMIN_USERNAME)}&exact=true`))[0];
  if (!user) throw new Error(`${ADMIN_USERNAME} is not a user of this realm`);
  say(`finishing the realm for ${ADMIN_USERNAME} (${user.id})`);

  const realm = await api('GET', '');
  if (!realm.organizationsEnabled) {
    if (CHECK) say('  organisations on the realm: DISABLED');
    else { await api('PUT', '', { ...realm, organizationsEnabled: true }); say('  organisations on the realm: enabled'); }
  } else say('  organisations on the realm: already enabled');

  // The realm role `admin` — Keycloak's own, for the operator's account.
  await ensure(
    `${ADMIN_USERNAME} holds the realm role admin`,
    async () => (await api('GET', `users/${user.id}/role-mappings/realm`)).find((r) => r.name === 'admin'),
    async () => {
      const role = await api('GET', 'roles/admin');
      await api('POST', `users/${user.id}/role-mappings/realm`, [role]);
    },
  );

  // The CLIENT role `identity:admin` — what makes them the AGENT's installation
  // administrator. The chart's own comment: the agent reads installation-wide
  // roles from that one client's namespace and ignores realm roles, so the realm
  // grant alone leaves the administrator holding nothing in the product.
  const identity = (await api('GET', 'clients?clientId=identity'))[0];
  if (!identity) throw new Error('no `identity` client in this realm');
  await ensure(
    'the identity client has an `admin` role',
    async () => (await api('GET', `clients/${identity.id}/roles`)).find((r) => r.name === 'admin'),
    () => api('POST', `clients/${identity.id}/roles`, {
      name: 'admin',
      description: 'Administers the installation: models, data sources, schema',
    }),
  );
  await ensure(
    `${ADMIN_USERNAME} holds identity:admin`,
    async () => (await api('GET', `users/${user.id}/role-mappings/clients/${identity.id}`)).find((r) => r.name === 'admin'),
    async () => {
      const role = await api('GET', `clients/${identity.id}/roles/admin`);
      await api('POST', `users/${user.id}/role-mappings/clients/${identity.id}`, [role]);
    },
  );

  const orgRoot = await ensure(
    'the /org group',
    async () => (await api('GET', 'groups?search=org&exact=true')).find((g) => g.path === '/org'),
    () => api('POST', 'groups', { name: 'org' }),
  );

  let first = true;
  for (const [name, alias] of ORGANIZATIONS) {
    say(`-- organisation ${alias} (${name})`);
    const org = await ensure(
      `  organisation ${alias}`,
      async () => (await api('GET', 'organizations')).find((o) => o.alias === alias),
      () => api('POST', 'organizations', {
        name, alias, enabled: true,
        // Keycloak refuses an organization with no domain and answers 400
        // without explaining.
        domains: [{ name: `${alias}.${APP_HOST}`, verified: true }],
      }),
    );
    if (!org) { first = false; continue; }

    const node = orgRoot ? await ensure(
      `  group /org/${org.id}`,
      async () => (await api('GET', `groups/${orgRoot.id}/children?search=${org.id}`)).find((g) => g.name === org.id),
      () => api('POST', `groups/${orgRoot.id}/children`, { name: org.id }),
    ) : null;
    const leaves = {};
    for (const leaf of ['admins', 'members']) {
      leaves[leaf] = node ? await ensure(
        `  group /org/${org.id}/${leaf}`,
        async () => (await api('GET', `groups/${node.id}/children?search=${leaf}`)).find((g) => g.name === leaf),
        () => api('POST', `groups/${node.id}/children`, { name: leaf }),
      ) : null;
    }

    // The administrator joins the FIRST organisation only, which is what the
    // chart does — and it is what decides which tenant the grounding Job grounds
    // into, so getting it wrong would ground the wrong tenant.
    if (first) {
      await ensure(
        `  ${ADMIN_USERNAME} is a member of ${alias}`,
        async () => (await api('GET', `organizations/${org.id}/members`)).find((m) => m.id === user.id),
        () => api('POST', `organizations/${org.id}/members`, user.id),
      );
      if (leaves.admins) {
        await ensure(
          `  ${ADMIN_USERNAME} is in /org/${org.id}/admins`,
          async () => (await api('GET', `users/${user.id}/groups`)).find((g) => g.id === leaves.admins.id),
          () => api('PUT', `users/${user.id}/groups/${leaves.admins.id}`),
        );
      }
    }
    first = false;
  }
  say(CHECK ? 'checked' : 'the realm is finished');
})().catch((e) => { console.log('FAILED', e.message); process.exit(1); });
