// The marketplace, from the agent host over its public API.
//
// Sign in once with MP_EMAIL / MP_PASSWORD; the bearer token is stored beside
// this file with mode 0600 and reused. Nothing here prints a credential.
const fs = require('node:fs');
const path = require('node:path');

const BASE = process.env.MP_BASE || 'https://marketplace.demo.superprotocol.dev';
const TOKENFILE = process.env.MP_TOKENFILE || path.join(__dirname, '.mp.token');

async function signIn() {
  const response = await fetch(`${BASE}/api/auth/sign-in/email`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Origin: BASE },
    body: JSON.stringify({ email: process.env.MP_EMAIL, password: process.env.MP_PASSWORD }),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok || !body || !body.token) {
    throw new Error(`sign-in ${response.status}`);
  }
  fs.writeFileSync(TOKENFILE, body.token, { mode: 0o600 });
  return body.token;
}

function token() {
  if (!fs.existsSync(TOKENFILE)) {
    throw new Error(`no token at ${TOKENFILE} — run: MP_EMAIL=… MP_PASSWORD=… node marketplace.js`);
  }
  return fs.readFileSync(TOKENFILE, 'utf8').trim();
}

async function api(method, route, body) {
  const response = await fetch(`${BASE}${route}`, {
    method,
    headers: {
      Authorization: `Bearer ${token()}`,
      Origin: BASE,
      ...(body ? { 'Content-Type': 'application/json' } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  return [response.status, await response.json().catch(() => null)];
}

module.exports = { BASE, api, signIn };

if (require.main === module) {
  signIn()
    .then(() => console.log(`signed in; token stored in ${path.basename(TOKENFILE)}`))
    .catch((error) => {
      console.log('FAILED', error.message);
      process.exit(1);
    });
}
