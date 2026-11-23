// Cypress Bay Digestive Health — scheduling system, MCP face.
//
// The far end of the Latica demonstration stand's one outbound channel. An
// autopilot inside the confidential deployment calls `schedule_recall` here and
// a surveillance appointment is written into the clinic's diary. There is one
// tool and it writes; the diary and the journal are behind a credential the
// clinic issues to its own staff, so nothing the DEPLOYMENT holds reads a
// patient record.
//
// Every call is admitted by two independent things, and the clinic owns both:
//
//   the token  — issued by the clinic, sent as `Authorization: Bearer …`,
//                carrying the scope the route needs, and for a caller granting
//                exactly one tool and the clinic's own six sites;
//   the code   — pulled and verified by the clinic once per ATTESTATION_TTL_MS,
//                against the digest the clinic approved (see attestation.mjs).
//                It binds the approved code to the caller's HOSTNAME; the token
//                is what binds the individual call. The README says so in the
//                same words, because the claim must not outrun the mechanism.
//
// Both are recorded, accepted or refused, in an append-only journal.
//
// The practice's own coordinators read the diary and the journal two ways: the
// JSON routes below, with their token as a bearer, and the screen in
// `staff.mjs`, which takes the same token once through a form and carries a
// session cookie. Same grant, same scope, one check — `grantFor(…, 'read')`.
import { createServer } from 'node:http';
import { createHash, timingSafeEqual } from 'node:crypto';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { z } from 'zod';
import { AttestationError, verifyCaller } from './attestation.mjs';
import { loadGrants } from './grants.mjs';
import { ClinicStore } from './store.mjs';
import {
  StaffSessions, appointmentsPage, clearedCookie, isSecureRequest, journalPage, loginPage,
  readCookie, sessionCookie,
} from './staff.mjs';

const SERVER_NAME = 'cypress-bay-scheduling';
const SERVER_VERSION = '0.1.0';
const PORT = Number(process.env.PORT || 8931);
const GRANTS_PATH = process.env.GRANTS_PATH || '/etc/clinic-scheduling/grants.json';
const DATA_DIR = process.env.DATA_DIR || '/var/lib/clinic-scheduling';
const ATTESTATION_TTL_MS = Number(process.env.ATTESTATION_TTL_MS || 60_000);

const { grants, pinnedRootFingerprint } = loadGrants(GRANTS_PATH);
const store = new ClinicStore(DATA_DIR);

const log = (level, event, fields = {}) =>
  console.log(JSON.stringify({ at: new Date().toISOString(), level, event, ...fields }));

/**
 * Constant-time lookup of a presented bearer among the tokens the clinic
 * issued, narrowed to the scope the route needs. A grant that does not carry
 * that scope is no grant at all here: the deployment's token opens `/mcp` and
 * cannot read the diary, and a staff token reads and cannot call a tool.
 */
function grantFor(authorization, scope) {
  const match = /^Bearer\s+(.+)$/i.exec(authorization ?? '');
  if (!match) return null;
  const presented = createHash('sha256').update(match[1].trim()).digest();
  for (const [token, grant] of grants) {
    const known = createHash('sha256').update(token).digest();
    if (timingSafeEqual(presented, known)) {
      return grant.scopes.includes(scope) ? grant : null;
    }
  }
  return null;
}

// One verification per grant per TTL: the clinic re-pulls the caller's evidence
// often enough that a change of code is caught within the minute, and not once
// per JSON-RPC frame.
const attestationCache = new Map();

async function attest(grant) {
  const key = `${grant.evidenceUrl}|${grant.approvedDigest}`;
  const cached = attestationCache.get(key);
  if (cached && Date.now() - cached.at < ATTESTATION_TTL_MS) {
    if (cached.error) throw cached.error;
    return cached.caller;
  }
  try {
    const caller = await verifyCaller({
      evidenceUrl: grant.evidenceUrl,
      approvedDigest: grant.approvedDigest,
      pinnedRootFingerprint,
    });
    attestationCache.set(key, { at: Date.now(), caller });
    return caller;
  } catch (error) {
    if (error instanceof AttestationError) attestationCache.set(key, { at: Date.now(), error });
    throw error;
  }
}

const SCHEDULE_INPUT = {
  recall_id: z.string().trim().min(3).max(40).describe("The clinic's recall identifier, e.g. RC-0004655."),
  mrn: z.string().trim().min(3).max(40).describe('Medical record number of the patient the recall is for.'),
  patient_name: z.string().trim().min(2).max(120).describe('Patient name as the clinic holds it, "Surname, Forename".'),
  site: z.string().trim().min(2).max(120).describe('The clinical site the appointment is at, as the practice names it.'),
  due_date: z.string().trim().regex(/^\d{4}-\d{2}-\d{2}$/).optional().describe('The date surveillance fell due, YYYY-MM-DD.'),
  days_overdue: z.number().int().min(0).max(20_000).optional().describe('How far past due, in days.'),
  reason: z.string().trim().min(10).max(400).describe('Why this patient is being recalled, in one sentence a coordinator can read.'),
  outreach_channel: z.enum(['telephone', 'letter', 'portal']).describe('How the clinic should reach the patient first.'),
  requested_by: z.string().trim().min(3).max(80).describe('The signal this recall came from, e.g. "SIG-41 · <signal id>".'),
  signal_url: z.string().trim().max(300).optional().describe('Link back to the signal card in the application.'),
};

function toolError(reason, detail) {
  return {
    isError: true,
    content: [{ type: 'text', text: JSON.stringify({ accepted: false, reason, detail: detail ?? null }) }],
  };
}

function buildServer(grant, caller, tenantId) {
  const server = new McpServer({ name: SERVER_NAME, version: SERVER_VERSION });
  server.registerTool(
    'schedule_recall',
    {
      title: 'Schedule a surveillance recall',
      description:
        'Book a surveillance colonoscopy recall in the Cypress Bay Digestive Health scheduling system for one named patient at one named site. ' +
        'Writes only; there is nothing here to read a patient record with. The call is admitted only from code the clinic has verified, and ' +
        'booking the same recall_id twice returns the appointment already made rather than a second slot.',
      inputSchema: SCHEDULE_INPUT,
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async (args) => {
      if (!grant.sites.includes(args.site)) {
        store.record({
          outcome: 'refused', reason: 'site_outside_grant', tool: 'schedule_recall',
          grant: grant.label, tenantId, site: args.site, recall_id: args.recall_id,
        });
        log('warn', 'call_refused', { reason: 'site_outside_grant', site: args.site });
        return toolError('site_outside_grant', `the token the clinic issued does not cover ${args.site}`);
      }
      const { appointment, outcome } = store.book(args, caller);
      store.record({
        outcome: 'accepted', tool: 'schedule_recall', result: outcome, grant: grant.label, tenantId,
        recall_id: args.recall_id, mrn: args.mrn, site: args.site,
        appointment_id: appointment.appointment_id,
        attested: { hostname: caller.hostname, evidence_digest: caller.evidenceDigest, cert_fingerprint: caller.certFingerprint },
      });
      log('info', 'call_accepted', { tool: 'schedule_recall', result: outcome, recall_id: args.recall_id, appointment: appointment.appointment_id });
      return {
        content: [{
          type: 'text',
          text: JSON.stringify({
            accepted: true, outcome,
            appointment_id: appointment.appointment_id,
            appointment_date: appointment.appointment_date,
            appointment_time: appointment.appointment_time,
            site: appointment.site, patient_name: appointment.patient_name, mrn: appointment.mrn,
            outreach_channel: appointment.outreach_channel,
            accepted_from_evidence_digest: caller.evidenceDigest,
          }, null, 1),
        }],
      };
    },
  );
  return server;
}

function sendJson(res, status, body, headers = {}) {
  res.writeHead(status, { 'content-type': 'application/json', ...headers });
  res.end(JSON.stringify(body, null, 1));
}

function rpcError(res, status, message, headers = {}) {
  sendJson(res, status, { jsonrpc: '2.0', error: { code: -32_000, message }, id: null }, headers);
}

async function handleMcp(req, res) {
  const grant = grantFor(req.headers.authorization, 'mcp');
  if (!grant) {
    store.record({ outcome: 'refused', reason: 'no_credential', path: '/mcp', method: req.method });
    log('warn', 'request_refused', { reason: 'no_credential' });
    rpcError(res, 401, 'Unauthorized: this system accepts only a token Cypress Bay issued', { 'www-authenticate': 'Bearer' });
    return;
  }
  if (req.method !== 'POST') {
    rpcError(res, 405, 'Method not allowed: this server is stateless', { allow: 'POST' });
    return;
  }

  let caller;
  try {
    caller = await attest(grant);
  } catch (error) {
    const reason = error instanceof AttestationError ? error.reason : 'attestation_failed';
    const detail = error instanceof AttestationError ? error.detail : String(error.message ?? error);
    store.record({ outcome: 'refused', reason, detail, grant: grant.label, evidenceUrl: grant.evidenceUrl, approvedDigest: grant.approvedDigest });
    log('warn', 'request_refused', { reason, detail, grant: grant.label });
    rpcError(res, 403, `Refused: ${reason}${detail ? ` (${detail})` : ''}`);
    return;
  }

  const tenantId = String(req.headers['x-aivo-tenant-id'] ?? '');
  const server = buildServer(grant, caller, tenantId);
  const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
  res.on('close', () => { void transport.close(); void server.close(); });
  await server.connect(transport);
  await transport.handleRequest(req, res);
}

const sessions = new StaffSessions();

function sendHtml(res, status, html, headers = {}) {
  res.writeHead(status, {
    'content-type': 'text/html; charset=utf-8',
    'cache-control': 'no-store',
    // The pages hold patient names, so they are not a document any
    // intermediary, extension or embedder should get to keep or frame.
    'referrer-policy': 'no-referrer',
    'x-content-type-options': 'nosniff',
    'content-security-policy': "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    ...headers,
  });
  res.end(html);
}

function seeOther(res, location, headers = {}) {
  res.writeHead(303, { location, 'cache-control': 'no-store', ...headers });
  res.end();
}

/**
 * An `application/x-www-form-urlencoded` body, capped: a token is at most a
 * hundred characters and nothing here has any use for more. Over the cap the
 * rest is DISCARDED rather than the socket destroyed — a request killed
 * mid-stream never receives the refusal, so the browser shows a network error
 * where it should show "that was too long to be a token".
 */
function readForm(req, limitBytes = 8 * 1024) {
  return new Promise((resolve, reject) => {
    let size = 0;
    let overflow = false;
    const chunks = [];
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > limitBytes) { overflow = true; return; }
      chunks.push(chunk);
    });
    req.on('end', () => resolve({
      overflow,
      form: new URLSearchParams(overflow ? '' : Buffer.concat(chunks).toString('utf8')),
    }));
    req.on('error', reject);
  });
}

/**
 * The screen. Two views, a sign-in form, a sign-out, and nothing else: there is
 * no route here that writes to the diary, because a window that can also book
 * is a second way into the scheduling system and the demonstration does not
 * need one.
 *
 * The credential check is `grantFor(…, 'read')` — the same call the JSON routes
 * make, against the same grants file. A session is only ever opened from a
 * token that already carries scope `read`, so the screen cannot show anything a
 * holder of that token could not already fetch.
 */
async function handleStaff(req, res, path, query) {
  const secure = isSecureRequest(req);

  if (req.method === 'POST' && path === '/staff/session') {
    const { form, overflow } = await readForm(req);
    if (overflow) {
      sendHtml(res, 413, loginPage({ error: 'That was too long to be a token.' }));
      return;
    }
    const presented = (form.get('token') ?? '').trim();
    const reader = grantFor(`Bearer ${presented}`, 'read');
    if (!reader) {
      // A refused credential on the clinic's own system is exactly what a
      // journal is for. A successful sign-in is not recorded: the journal is
      // the record of CALLS, and page views would bury the six entries the
      // demonstration turns on.
      store.record({ outcome: 'refused', reason: 'no_staff_credential', path, method: req.method });
      log('warn', 'request_refused', { reason: 'no_staff_credential', path });
      sendHtml(res, 401, loginPage({ error: 'That is not a token this practice issued for reading.' }));
      return;
    }
    const id = sessions.open(reader.label);
    log('info', 'staff_signed_in', { grant: reader.label });
    seeOther(res, '/staff/appointments', { 'set-cookie': sessionCookie(id, { secure }) });
    return;
  }

  if (req.method === 'POST' && path === '/staff/sign-out') {
    const id = readCookie(req.headers.cookie);
    sessions.close(id);
    seeOther(res, '/staff', { 'set-cookie': clearedCookie({ secure }) });
    return;
  }

  if (req.method !== 'GET') {
    sendHtml(res, 405, loginPage({ error: 'Method not allowed.' }), { allow: 'GET, POST' });
    return;
  }

  const session = sessions.get(readCookie(req.headers.cookie));
  if (!session) {
    // A cookie that has expired is cleared on the way out, so the browser stops
    // sending a value this process will never recognise again.
    sendHtml(res, 200, loginPage(), { 'set-cookie': clearedCookie({ secure }) });
    return;
  }
  if (path === '/staff/journal') {
    // Default to the calls the attestation check actually ran on; `?all=1` is
    // the whole append-only record, which is one link away because
    // append-only is part of what this page is evidence of.
    sendHtml(res, 200, journalPage(store.journal(), session, { showAll: query.get('all') === '1' }));
    return;
  }
  if (path === '/staff/appointments') {
    sendHtml(res, 200, appointmentsPage(store.appointments(), session));
    return;
  }
  seeOther(res, '/staff/appointments');
}

const server = createServer({ maxHeaderSize: 64 * 1024 }, (req, res) => {
  const url = new URL(req.url ?? '/', 'http://clinic.invalid');
  const path = url.pathname;
  if (req.method === 'GET' && path === '/healthz') {
    const callers = [...grants.values()].filter((g) => g.scopes.includes('mcp')).length;
    sendJson(res, 200, {
      status: 'ok', server: SERVER_NAME, version: SERVER_VERSION,
      grants: grants.size, callers, readers: grants.size - callers,
    });
    return;
  }
  // The diary and the journal, for the practice's own staff and for nobody
  // else. A SEPARATE credential on purpose: the diary holds names and medical
  // record numbers and the journal holds every grant's history, so the
  // deployment's token must not open them — otherwise the one tool it was given
  // would be a way back into the chart after all.
  if (req.method === 'GET' && (path === '/appointments' || path === '/journal')) {
    const reader = grantFor(req.headers.authorization, 'read');
    if (!reader) {
      store.record({ outcome: 'refused', reason: 'no_read_credential', path, method: req.method });
      log('warn', 'request_refused', { reason: 'no_read_credential', path });
      sendJson(res, 401, { error: 'unauthorized: these routes need a credential Cypress Bay issued to its own staff' }, { 'www-authenticate': 'Bearer' });
      return;
    }
    sendJson(res, 200, path === '/appointments' ? store.appointments() : store.journal());
    return;
  }
  // The screen. `/` lands here because a person who is handed this hostname
  // types no path, and `/staff` without a view is the diary.
  if (path === '/' || path === '/staff' || path.startsWith('/staff/')) {
    handleStaff(req, res, path === '/' || path === '/staff' ? '/staff/appointments' : path, url.searchParams).catch((error) => {
      log('error', 'request_failed', { error: error?.message ?? 'Error', path });
      if (!res.headersSent) sendHtml(res, 500, loginPage({ error: 'Something went wrong on this system.' }));
      else res.end();
    });
    return;
  }
  // Browsers ask for this unprompted; answering 404 with a JSON body is noise
  // in the log of a system whose log is evidence.
  if (req.method === 'GET' && path === '/favicon.ico') {
    res.writeHead(204, { 'cache-control': 'public, max-age=86400' });
    res.end();
    return;
  }
  if (path === '/mcp') {
    handleMcp(req, res).catch((error) => {
      log('error', 'request_failed', { error: error?.message ?? 'Error' });
      if (!res.headersSent) rpcError(res, 500, 'Internal server error');
      else res.end();
    });
    return;
  }
  sendJson(res, 404, { error: 'not found' });
});
// The port that was actually bound, not the one that was asked for: `PORT=0`
// means "whichever is free", and the tests read this line to find the server.
server.listen(PORT, () =>
  log('info', 'listening', { port: server.address().port, grants: grants.size, dataDir: DATA_DIR }));
