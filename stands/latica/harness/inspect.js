const fs = require('fs');
const { signIn, graphql } = require('./applogin');
const Q = `query {
  availableEvents { id name description systemKey }
  mcpServers { id slug enabled tools { name boundName readOnly } }
  autopilots { id name enabled cronExpression signalMode allowedMcpTools visibility }
  llmModels { id name isDefault }
}`;
(async () => {
  const base = process.env.APP_BASE;
  let cookie;
  for (let a = 1; a <= 5; a += 1) {
    try { cookie = await signIn({ base, username: process.env.APP_USER, password: fs.readFileSync(process.env.PASS_FILE, 'utf8').trim() }); break; }
    catch (e) { if (a === 5) throw e; await new Promise((r) => setTimeout(r, 6000)); }
  }
  const d = await graphql({ base, cookie, query: Q });
  console.log(JSON.stringify(d, null, 1));
})().catch((e) => { console.log('FAILED', e.message); process.exit(1); });
