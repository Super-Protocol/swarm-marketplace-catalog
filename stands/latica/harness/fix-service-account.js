// Finish what the realm bootstrap could not: give the `identity` client's
// service account the realm-management roles the agent's identity integration
// needs. Run against a deployment's own Keycloak, as its master admin.
//
//   KC_BASE=https://<host>/auth KC_PASS_FILE=./<name>.internalSecret node fix-service-account.js
//   … CHECK=1 node fix-service-account.js        # report, change nothing
const fs = require('node:fs');
const BASE = process.env.KC_BASE.replace(/\/$/, '');
const REALM = process.env.KC_REALM || 'master';
const CHECK = Boolean(process.env.CHECK);
const WANTED = ['manage-realm', 'manage-users', 'view-users', 'query-users', 'query-groups',
  'manage-clients', 'view-clients', 'view-realm'];

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
      headers: { Authorization: `Bearer ${access}`, ...(payload ? { 'Content-Type': 'application/json' } : {}) },
      ...(payload ? { body: JSON.stringify(payload) } : {}),
    });
    const text = await r.text();
    if (r.status === 401) { access = await token(); return api(method, path, payload); }
    if (!r.ok) throw new Error(`${method} ${path} ${r.status} ${text.slice(0, 200)}`);
    return text ? JSON.parse(text) : null;
  };

  const identity = (await api('GET', 'clients?clientId=identity'))[0];
  if (!identity) throw new Error('no `identity` client in this realm');
  const account = await api('GET', `clients/${identity.id}/service-account-user`);
  const realmClient = (await api('GET', `clients?clientId=${encodeURIComponent(`${REALM}-realm`)}`))[0];
  if (!realmClient) throw new Error(`no \`${REALM}-realm\` client`);

  const available = await api('GET', `users/${account.id}/role-mappings/clients/${realmClient.id}/available`);
  const held = await api('GET', `users/${account.id}/role-mappings/clients/${realmClient.id}`);
  const have = new Set(held.map((r) => r.name));
  console.log(`service-account-${identity.clientId} (${account.id})`);
  console.log(`  holds   ${JSON.stringify([...have].sort())}`);
  const missing = WANTED.filter((name) => !have.has(name));
  console.log(`  missing ${JSON.stringify(missing)}`);
  if (missing.length === 0 || CHECK) return;

  const byName = new Map(available.map((r) => [r.name, r]));
  const grant = missing.map((name) => byName.get(name)).filter(Boolean);
  if (grant.length !== missing.length) {
    console.log(`  !! ${missing.filter((n) => !byName.has(n))} are not offered by ${REALM}-realm`);
  }
  if (grant.length > 0) {
    await api('POST', `users/${account.id}/role-mappings/clients/${realmClient.id}`, grant);
    console.log(`  granted ${JSON.stringify(grant.map((r) => r.name))}`);
  }
  const after = new Set((await api('GET', `users/${account.id}/role-mappings/clients/${realmClient.id}`)).map((r) => r.name));
  const still = WANTED.filter((name) => !after.has(name));
  console.log(still.length === 0 ? '  all of them are in place' : `  STILL missing ${JSON.stringify(still)}`);
  if (still.length > 0) process.exitCode = 1;
})().catch((e) => { console.log('FAILED', e.message); process.exit(1); });
