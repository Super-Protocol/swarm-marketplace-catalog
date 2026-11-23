// One dialog turn against the stand, optionally with files attached, read back
// the way a person sees it: the saved messages after the stream ends.
//
//   APP_BASE=… APP_USER=… PASS_FILE=… QUESTION='…' [FILES=a.pdf,b.txt] [TAG=x] node run.js
const fs = require('fs');
const path = require('path');
const { signIn, graphql } = require('./applogin');
const { uploadFile } = require('./upload');

const CREATE = 'mutation { createThread { thread_id } }';
const SEND = `mutation ($threadId: String!, $text: String!, $correlationId: String, $context: JSON) {
  sendMessage(threadId: $threadId, text: $text, correlationId: $correlationId, context: $context) { __typename }
}`;
const READ = `query ($threadId: String!) {
  thread(threadId: $threadId) {
    status activeCorrelationId
    messages { id type content status isError errorType toolName toolCalls
      usage { inputTokens outputTokens totalTokens } }
  }
}`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function connect(base, user, passFile) {
  for (let attempt = 1; attempt <= 5; attempt += 1) {
    try {
      return await signIn({ base, username: user, password: fs.readFileSync(passFile, 'utf8').trim() });
    } catch (error) {
      console.log(`  sign-in attempt ${attempt}: ${error.message.slice(0, 110)}`);
      if (attempt === 5) throw error;
      await sleep(6000);
    }
  }
}

(async () => {
  const base = process.env.APP_BASE;
  const tag = process.env.TAG || 'run';
  const cookie = await connect(base, process.env.APP_USER, process.env.PASS_FILE);

  const context = {};
  if (process.env.FILES) {
    context.files = [];
    for (const file of process.env.FILES.split(',').map((s) => s.trim()).filter(Boolean)) {
      const uploaded = await uploadFile({ base, cookie, file });
      console.log(`  attached ${path.basename(file)} → ${uploaded.token} (${uploaded.size} bytes, ${uploaded.mimeType})`);
      context.files.push({
        token: uploaded.token, filename: uploaded.filename,
        mimeType: uploaded.mimeType, size: uploaded.size,
      });
    }
  }

  const thread = process.env.THREAD_ID
    || (await graphql({ base, cookie, query: CREATE })).createThread.thread_id;
  const correlationId = `sup712-${Date.now()}`;
  console.log('thread', thread, 'correlation', correlationId);
  console.log('Q:', process.env.QUESTION);
  const started = Date.now();
  await graphql({
    base, cookie, query: SEND,
    variables: { threadId: thread, text: process.env.QUESTION, correlationId,
      context: Object.keys(context).length ? context : undefined },
  });

  let last = '';
  for (let attempt = 0; attempt < Number(process.env.TRIES || 90); attempt += 1) {
    await sleep(5000);
    const t = (await graphql({ base, cookie, query: READ, variables: { threadId: thread } })).thread;
    const line = `${t.status} active=${t.activeCorrelationId || '—'} messages=${t.messages.length}`;
    if (line !== last) { console.log(`  [${((Date.now() - started) / 1000).toFixed(0)}s] ${line}`); last = line; }
    if (!t.activeCorrelationId && t.messages.some((m) => (m.type || '').toUpperCase() !== 'HUMAN')) {
      fs.writeFileSync(`${__dirname}/${tag}-thread.json`, JSON.stringify(t, null, 1));
      fs.writeFileSync(`${__dirname}/${tag}-thread.id`, thread);
      for (const m of t.messages) {
        console.log('---', m.type, m.status || '', m.isError ? `ERROR ${m.errorType || ''}` : '', m.toolName || '');
        if (m.toolCalls) console.log('    toolCalls:', JSON.stringify(m.toolCalls).slice(0, Number(process.env.TOOLCLIP || 600)));
        console.log('   ', (m.content || '').slice(0, Number(process.env.CLIP || 2600)).replace(/\n/g, '\n    '));
      }
      return;
    }
  }
  console.log('the answer did not settle inside the window');
  process.exit(1);
})().catch((e) => { console.log('FAILED', e.message); process.exit(1); });
