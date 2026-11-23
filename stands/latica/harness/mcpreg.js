// Register the clinic's scheduling system as the clinic organisation's own tool
// server, the way its administrator does it in the product: add, check, enable.
//
// The description is the wording this stand is required to carry, so it reads
// beside the address in the UI rather than only in a document.
const fs = require('fs');
const { signIn, graphql } = require('./applogin');

const EGRESS_WORDING =
  'Outbound traffic carries only the action the clinic authorised, for the patient it authorised, ' +
  'and only from code the clinic verified. Data never leaves. The token is Cypress Bay’s own and ' +
  'grants one tool — schedule_recall — at the practice’s six sites; there is nothing here to read a ' +
  'record with, and the clinic re-verifies this deployment’s signed evidence on every call.';

const LIST = `query { mcpServers { id name slug url enabled global enabledForTenant description toolCount tools { name boundName readOnly } lastError } }`;
const CREATE = `mutation ($input: CreateMcpServerInputDTO!) {
  createMcpServer(input: $input) { id name slug url enabled description }
}`;
const CHECK = `mutation ($input: CheckMcpServerInputDTO!) { checkMcpServer(input: $input) { ok error tools { name description } } }`;
const ENABLE = `mutation ($id: ID!, $enabled: Boolean!) { setMcpServerEnabled(id: $id, enabled: $enabled) { id enabled } }`;

(async () => {
  const base = process.env.APP_BASE;
  let cookie;
  for (let attempt = 1; attempt <= 5; attempt += 1) {
    try {
      cookie = await signIn({ base, username: process.env.APP_USER, password: fs.readFileSync(process.env.PASS_FILE, 'utf8').trim() });
      break;
    } catch (error) {
      console.log(`  sign-in ${attempt}: ${error.message.slice(0, 110)}`);
      if (attempt === 5) throw error;
      await new Promise((r) => setTimeout(r, 6000));
    }
  }

  const before = await graphql({ base, cookie, query: LIST });
  console.log('servers before:', JSON.stringify(before.mcpServers.map((s) => ({ slug: s.slug, enabled: s.enabled, global: s.global, tools: s.toolCount }))));

  // The token the clinic issued. A file, never an argument: it must not reach a
  // shell history or a process list.
  const token = fs.readFileSync(process.env.TOKEN_FILE, 'utf8').trim();
  const input = {
    name: 'Cypress Bay scheduling',
    slug: 'scheduling',
    url: process.env.MCP_URL,
    description: EGRESS_WORDING,
    enabled: false,
    authHeaders: [{ name: 'Authorization', value: `Bearer ${token}` }],
  };

  let server = before.mcpServers.find((s) => s.slug === 'scheduling');
  if (!server) {
    const checked = await graphql({ base, cookie, query: CHECK, variables: { input: { slug: input.slug, url: input.url, authHeaders: input.authHeaders } } });
    console.log('check:', JSON.stringify({ ok: checked.checkMcpServer.ok, error: checked.checkMcpServer.error, tools: (checked.checkMcpServer.tools || []).map((t) => t.name) }));
    if (!checked.checkMcpServer.ok) process.exit(1);
    server = (await graphql({ base, cookie, query: CREATE, variables: { input } })).createMcpServer;
    console.log('created:', JSON.stringify({ id: server.id, slug: server.slug, url: server.url }));
  }

  const enabled = await graphql({ base, cookie, query: ENABLE, variables: { id: server.id, enabled: true } });
  console.log('enabled:', JSON.stringify(enabled.setMcpServerEnabled));

  const after = await graphql({ base, cookie, query: LIST });
  for (const s of after.mcpServers) {
    console.log(`  ${s.slug} enabled=${s.enabled} global=${s.global} tools=${s.toolCount} [${(s.tools || []).map((t) => t.name).join(', ')}] lastError=${s.lastError ?? '—'}`);
    if (s.slug === 'scheduling') console.log('  description:', s.description);
  }
})().catch((e) => { console.log('FAILED', e.message); process.exit(1); });
