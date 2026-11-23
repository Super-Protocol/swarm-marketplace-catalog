// Flip the publisher's schedule, so the unattended path can be observed in a
// few minutes rather than tomorrow morning, and put it back afterwards.
const fs = require('fs');
const { signIn, graphql } = require('./applogin');
const UPDATE = `mutation ($id: ID!, $input: UpdateAutopilotInput!) { updateAutopilot(id: $id, input: $input) { id name cronExpression enabled } }`;
(async () => {
  const base = process.env.APP_BASE;
  let cookie;
  for (let a = 1; a <= 5; a += 1) {
    try { cookie = await signIn({ base, username: process.env.APP_USER, password: fs.readFileSync(process.env.PASS_FILE, 'utf8').trim() }); break; }
    catch (e) { if (a === 5) throw e; await new Promise((r) => setTimeout(r, 6000)); }
  }
  const id = process.env.PUBLISHER_ID || fs.readFileSync(`${__dirname}/autopilot-surveillance-overdue.id`, 'utf8').trim();
  const r = await graphql({ base, cookie, query: UPDATE, variables: { id, input: { cronExpression: process.env.CRON } } });
  console.log(JSON.stringify(r.updateAutopilot));
})().catch((e) => { console.log('FAILED', e.message); process.exit(1); });
