// Signs in to the stand the way a browser does — oauth2-proxy → Keycloak form →
// callback — and returns the proxy's session cookie, so every later call goes
// through the application's own front door rather than around it.
const fs = require('fs');

const jar = new Map();
function remember(response) {
  for (const line of response.headers.getSetCookie ? response.headers.getSetCookie() : []) {
    const [pair] = line.split(';');
    const i = pair.indexOf('=');
    const name = pair.slice(0, i).trim();
    const value = pair.slice(i + 1).trim();
    if (value === '' || /Max-Age=0/i.test(line)) jar.delete(name);
    else jar.set(name, value);
  }
}
function cookieHeader() {
  return [...jar.entries()].map(([k, v]) => `${k}=${v}`).join('; ');
}

async function go(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    redirect: 'manual',
    headers: { Cookie: cookieHeader(), ...(options.headers || {}) },
  });
  remember(response);
  return response;
}

/** Follow redirects by hand so the cookie jar is carried across hosts. */
async function chase(url, options = {}, hops = 12) {
  let current = url;
  let response = await go(current, options);
  for (let i = 0; i < hops && [301, 302, 303, 307, 308].includes(response.status); i += 1) {
    current = new URL(response.headers.get('location'), current).toString();
    response = await go(current, { headers: options.headers });
  }
  return [response, current];
}

async function signIn({ base, username, password }) {
  jar.clear();
  const [form, formUrl] = await chase(`${base}/oauth2/start?rd=%2F`);
  const html = await form.text();
  if (form.status !== 200) throw new Error(`login form ${form.status} at ${formUrl}`);
  const action = (html.match(/action="([^"]+)"/) || [])[1];
  if (!action) throw new Error(`no form on the sign-in page: ${html.slice(0, 300)}`);
  const post = new URL(action.replace(/&amp;/g, '&'), formUrl).toString();
  const [after, afterUrl] = await chase(post, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ username, password, credentialId: '' }).toString(),
  });
  if (after.status !== 200) {
    throw new Error(`sign-in ended ${after.status} at ${afterUrl}`);
  }
  const body = await after.text();
  if (/Invalid username or password|Invalid user credentials/i.test(body)) {
    throw new Error('Keycloak refused the credentials');
  }
  if (!jar.has('_oauth2_proxy')) throw new Error(`no proxy session; cookies: ${[...jar.keys()].join(',')}`);
  return cookieHeader();
}

async function graphql({ base, query, variables, cookie }) {
  const response = await fetch(`${base}/graphql`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Cookie: cookie },
    body: JSON.stringify({ query, variables }),
  });
  const text = await response.text();
  let body;
  try { body = JSON.parse(text); } catch { throw new Error(`graphql ${response.status}: ${text.slice(0, 300)}`); }
  if (body.errors) throw new Error(`graphql errors: ${JSON.stringify(body.errors).slice(0, 600)}`);
  return body.data;
}

module.exports = { signIn, graphql };

if (require.main === module) {
  (async () => {
    const base = process.env.APP_BASE;
    const cookie = await signIn({
      base,
      username: process.env.APP_USER,
      password: fs.readFileSync(process.env.PASS_FILE, 'utf8').trim(),
    });
    console.log('signed in to', base);
    const me = await graphql({ base, cookie, query: '{ llmModels { id name isDefault } }' });
    console.log('models visible in chat:', JSON.stringify(me.llmModels));
  })().catch((e) => { console.log('FAILED', e.message); process.exit(1); });
}
