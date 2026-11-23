const fs = require('fs');
const { signIn, graphql } = require('./applogin');
const CHECK = `mutation ($input: CheckMcpServerInputDTO!) { checkMcpServer(input: $input) { ok error tools { name } } }`;
const LIST = `query { mcpServers { id slug enabled lastError lastCheckedAt toolCount } }`;
(async () => {
  const base = process.env.APP_BASE;
  let cookie;
  for (let a = 1; a <= 5; a += 1) {
    try { cookie = await signIn({ base, username: process.env.APP_USER, password: fs.readFileSync(process.env.PASS_FILE, 'utf8').trim() }); break; }
    catch (e) { if (a === 5) throw e; await new Promise((r) => setTimeout(r, 6000)); }
  }
  const server = (await graphql({ base, cookie, query: LIST })).mcpServers.find((s) => s.slug === 'scheduling');
  console.log('before:', JSON.stringify(server));
  console.log('check:', JSON.stringify((await graphql({ base, cookie, query: CHECK, variables: { input: { id: server.id } } })).checkMcpServer));
  console.log('after:', JSON.stringify((await graphql({ base, cookie, query: LIST })).mcpServers.find((s) => s.slug === 'scheduling')));
})().catch((e) => { console.log('FAILED', e.message); process.exit(1); });
