// Run the subscribed autopilot on demand, with the signal it would have been
// handed by the event. Same autopilot, same prompt, source MANUAL instead of
// EVENT — which is the "and on demand" half of the acceptance criterion.
const fs = require('fs');
const { signIn, graphql } = require('./applogin');

const TRIGGER = `mutation ($id: ID!, $input: TriggerAutopilotInput) { triggerAutopilot(id: $id, input: $input) }`;
const RUNS = `query ($id: ID!) { autopilotRuns(autopilotId: $id, limit: 4) { threadId source tags args title isError response createdAt } }`;
const SIGNAL = `query ($id: ID!) { signal(id: $id) { id code priority status occurrences title scope scopeLabels url: cardMarkdown } }`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const base = process.env.APP_BASE;
  let cookie;
  for (let a = 1; a <= 5; a += 1) {
    try { cookie = await signIn({ base, username: process.env.APP_USER, password: fs.readFileSync(process.env.PASS_FILE, 'utf8').trim() }); break; }
    catch (e) { if (a === 5) throw e; await sleep(6000); }
  }
  const booker = process.env.BOOKER_ID || fs.readFileSync(`${__dirname}/autopilot-book-the-recall.id`, 'utf8').trim();
  const args = JSON.parse(fs.readFileSync(`${__dirname}/${process.env.ARGS_FILE}`, 'utf8'));
  console.log('trigger', (await graphql({ base, cookie, query: TRIGGER, variables: { id: booker, input: { args, tags: [process.env.TAG || 'ondemand'] } } })).triggerAutopilot);

  const before = (await graphql({ base, cookie, query: RUNS, variables: { id: booker } })).autopilotRuns.length;
  for (let attempt = 0; attempt < Number(process.env.TRIES || 40); attempt += 1) {
    await sleep(15000);
    let runs;
    try { runs = (await graphql({ base, cookie, query: RUNS, variables: { id: booker } })).autopilotRuns; }
    catch (e) { console.log(`  poll failed: ${e.message.slice(0, 70)}`); continue; }
    const latest = runs[0];
    console.log(`[${attempt}] runs=${runs.length} latest=${latest && `${latest.source}/${latest.isError ? 'error' : latest.response ? 'answered' : 'running'}`}`);
    if (latest && (latest.response || latest.isError) && latest.source === 'MANUAL') {
      fs.writeFileSync(`${__dirname}/${process.env.TAG || 'ondemand'}-booker-runs.json`, JSON.stringify(runs, null, 1));
      console.log('\n=== on-demand run');
      console.log(JSON.stringify({ source: latest.source, tags: latest.tags, isError: latest.isError, title: latest.title }, null, 1));
      console.log(latest.response);
      return;
    }
  }
  console.log('window elapsed'); process.exit(1);
})().catch((e) => { console.log('FAILED', e.message); process.exit(1); });
