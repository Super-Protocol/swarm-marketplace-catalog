// The two autopilots the recall scenario is built from, created through the
// product's own API: one publishes a signal, the other is subscribed to that
// signal and calls the clinic's scheduling system.
const fs = require('fs');
const { signIn, graphql } = require('./applogin');

const BOUND_TOOL = 'mcp_scheduling_schedule_recall';

const EVENTS = `query { availableEvents { id name systemKey } }`;
const LIST = `query { autopilots { id name enabled cronExpression signalMode signalBlock allowedMcpTools visibility } }`;
const CREATE = `mutation ($input: CreateAutopilotInput!) {
  createAutopilot(input: $input) { id name enabled cronExpression signalMode allowedMcpTools visibility }
}`;
const UPDATE = `mutation ($id: ID!, $input: UpdateAutopilotInput!) {
  updateAutopilot(id: $id, input: $input) { id name enabled cronExpression signalMode allowedMcpTools }
}`;
const SUBS = `query ($id: ID!) { autopilot(id: $id) { id name eventSubscriptions { eventId eventName daysOfWeek signalFilter { codes minPriority autopilotIds scope { key value } } } promptActionWarnings { index tool code param } } }`;

const read = (name) => fs.readFileSync(`${__dirname}/../autopilots/${name}`, 'utf8');

const plan = (signalCreatedEventId) => [
  {
    name: 'Surveillance overdue',
    promptTemplate: read('surveillance-overdue.njk'),
    enabled: true,
    // 07:10 every weekday, in the tenant's timezone: the unattended run.
    cronExpression: '10 7 * * 1-5',
    visibility: 'TENANT',
    signalMode: true,
    signalBlock: 'OTHER',
    allowedMcpTools: [],
    parameters: [{ name: 'site', title: 'One site only (blank = the whole practice)', type: 'string', required: false }],
  },
  {
    name: 'Book the recall',
    promptTemplate: read('book-recall.njk'),
    enabled: true,
    cronExpression: null,
    visibility: 'TENANT',
    signalMode: false,
    allowedMcpTools: [BOUND_TOOL],
    eventSubscriptions: [{ eventId: signalCreatedEventId, signalFilter: { codes: ['SURV-01'] } }],
  },
];

(async () => {
  const base = process.env.APP_BASE;
  let cookie;
  for (let a = 1; a <= 5; a += 1) {
    try { cookie = await signIn({ base, username: process.env.APP_USER, password: fs.readFileSync(process.env.PASS_FILE, 'utf8').trim() }); break; }
    catch (e) { console.log(`  sign-in ${a}: ${e.message.slice(0, 100)}`); if (a === 5) throw e; await new Promise((r) => setTimeout(r, 6000)); }
  }

  const events = (await graphql({ base, cookie, query: EVENTS })).availableEvents;
  const signalCreated = events.find((e) => e.systemKey === 'signal.created');
  if (!signalCreated) throw new Error('this installation publishes no signal.created event');
  console.log(`signal.created event: ${signalCreated.id}`);

  const existing = (await graphql({ base, cookie, query: LIST })).autopilots;
  for (const wanted of plan(signalCreated.id)) {
    const found = existing.find((a) => a.name === wanted.name);
    const { name, ...rest } = wanted;
    const result = found
      ? (await graphql({ base, cookie, query: UPDATE, variables: { id: found.id, input: rest } })).updateAutopilot
      : (await graphql({ base, cookie, query: CREATE, variables: { input: wanted } })).createAutopilot;
    console.log(`${found ? 'updated' : 'created'}: ${JSON.stringify(result)}`);
    const read = (await graphql({ base, cookie, query: SUBS, variables: { id: result.id } })).autopilot;
    console.log(`  subscriptions: ${JSON.stringify(read.eventSubscriptions)}`);
    console.log(`  warnings:      ${JSON.stringify(read.promptActionWarnings)}`);
    fs.writeFileSync(`${__dirname}/autopilot-${result.name.replace(/\W+/g, '-').toLowerCase()}.id`, result.id);
  }
})().catch((e) => { console.log('FAILED', e.message); process.exit(1); });
