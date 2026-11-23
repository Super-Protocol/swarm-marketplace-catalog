// Run an autopilot now and watch what came of it: the run history with its
// source, the signal it published, and the subscribed autopilot's own run.
const fs = require('fs');
const { signIn, graphql } = require('./applogin');

const TRIGGER = `mutation ($id: ID!, $input: TriggerAutopilotInput) { triggerAutopilot(id: $id, input: $input) }`;
const RUNS = `query ($id: ID!, $limit: Int!) {
  autopilotRuns(autopilotId: $id, limit: $limit) {
    threadId source tags args title isError isSilent emittedSignalIds createdAt
    response notStarted { code level } actionWarnings { tool label reason }
  }
}`;
const SIGNALS = `query {
  signals(paging: { limit: 20, offset: 0 }) {
    total
    items { id code kind priority status title occurrences scope scopeLabels block
      firstSeenAt lastSeenAt firstThreadId lastThreadId autopilotName
      evidence { kind label value } }
  }
}`;
const EVENTS = `query ($id: ID!) { signalEvents(signalId: $id, limit: 50) { type actorType actorName threadId createdAt payload } }`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function connect(base) {
  for (let a = 1; a <= 5; a += 1) {
    try { return await signIn({ base, username: process.env.APP_USER, password: fs.readFileSync(process.env.PASS_FILE, 'utf8').trim() }); }
    catch (e) { console.log(`  sign-in ${a}: ${e.message.slice(0, 100)}`); if (a === 5) throw e; await sleep(6000); }
  }
}

(async () => {
  const base = process.env.APP_BASE;
  let cookie = await connect(base);
  // Written by autopilots.js next to itself; PUBLISHER_ID / BOOKER_ID override.
  const publisher = process.env.PUBLISHER_ID || fs.readFileSync(`${__dirname}/autopilot-surveillance-overdue.id`, 'utf8').trim();
  const booker = process.env.BOOKER_ID || fs.readFileSync(`${__dirname}/autopilot-book-the-recall.id`, 'utf8').trim();

  if (process.env.TRIGGER !== 'no') {
    const args = process.env.ARGS ? JSON.parse(process.env.ARGS) : {};
    console.log('trigger', (await graphql({ base, cookie, query: TRIGGER, variables: { id: publisher, input: { args, tags: [process.env.TAG || 'sup712'] } } })).triggerAutopilot);
  }

  const started = Date.now();
  for (let attempt = 0; attempt < Number(process.env.TRIES || 60); attempt += 1) {
    await sleep(15000);
    // The cloud gateway answers 502 on the odd hop; a poll that dies on one
    // loses the measurement it was taking.
    let pub;
    let bok;
    try {
      pub = (await graphql({ base, cookie, query: RUNS, variables: { id: publisher, limit: 5 } })).autopilotRuns;
      bok = (await graphql({ base, cookie, query: RUNS, variables: { id: booker, limit: 5 } })).autopilotRuns;
    } catch (error) {
      console.log(`  poll failed, reconnecting: ${error.message.slice(0, 80)}`);
      cookie = await connect(base);
      continue;
    }
    console.log(`[${((Date.now() - started) / 1000).toFixed(0)}s] publisher runs=${pub.length} latest=${pub[0] && `${pub[0].source}/${pub[0].isError ? 'error' : pub[0].response ? 'answered' : 'running'}/signals=${(pub[0].emittedSignalIds || []).length}`} | booker runs=${bok.length} latest=${bok[0] && `${bok[0].source}/${bok[0].isError ? 'error' : bok[0].response ? 'answered' : 'running'}`}`);
    const done = pub[0] && (pub[0].response || pub[0].isError) && (bok.length === 0 || bok[0].response || bok[0].isError);
    if (done && attempt >= 1) {
      fs.writeFileSync(`${__dirname}/${process.env.TAG || 'fire'}-publisher-runs.json`, JSON.stringify(pub, null, 1));
      fs.writeFileSync(`${__dirname}/${process.env.TAG || 'fire'}-booker-runs.json`, JSON.stringify(bok, null, 1));
      const sig = (await graphql({ base, cookie, query: SIGNALS })).signals;
      fs.writeFileSync(`${__dirname}/${process.env.TAG || 'fire'}-signals.json`, JSON.stringify(sig, null, 1));
      console.log('\n=== publisher run');
      console.log(JSON.stringify({ source: pub[0].source, tags: pub[0].tags, args: pub[0].args, title: pub[0].title, isError: pub[0].isError, isSilent: pub[0].isSilent, signals: pub[0].emittedSignalIds }, null, 1));
      console.log(pub[0].response);
      console.log('\n=== signals feed, total', sig.total);
      for (const s of sig.items) {
        console.log(JSON.stringify({ id: s.id, code: s.code, kind: s.kind, priority: s.priority, status: s.status, occurrences: s.occurrences, block: s.block, scope: s.scope, scopeLabels: s.scopeLabels, title: s.title, firstSeenAt: s.firstSeenAt, lastSeenAt: s.lastSeenAt, firstThreadId: s.firstThreadId, lastThreadId: s.lastThreadId }, null, 1));
        const ev = (await graphql({ base, cookie, query: EVENTS, variables: { id: s.id } })).signalEvents;
        fs.writeFileSync(`${__dirname}/${process.env.TAG || 'fire'}-signal-events.json`, JSON.stringify(ev, null, 1));
        console.log('  events:', JSON.stringify(ev.map((e) => [e.type, e.actorType, e.actorName, e.createdAt])));
      }
      if (bok[0]) {
        console.log('\n=== booker run');
        console.log(JSON.stringify({ source: bok[0].source, args: bok[0].args, isError: bok[0].isError, title: bok[0].title }, null, 1));
        console.log(bok[0].response);
      } else console.log('\n=== booker never ran');
      return;
    }
  }
  console.log('window elapsed');
  process.exit(1);
})().catch((e) => { console.log('FAILED', e.message); process.exit(1); });
