// The practice's own screen over the diary and the journal.
//
// The JSON routes are what the autopilot and a `curl` use; this is what a
// coordinator uses, and what the moment the demonstration is built around —
// *the booking arrived in the clinic's own system* — is shown on, instead of a
// terminal. Two views, both read-only, both behind the SAME staff grant the JSON
// routes want: `grantFor(…, 'read')` from `grants.mjs` is the only credential
// check here, so a screen cannot acquire a right the token does not carry.
//
// **Not HTTP Basic**, deliberately. A browser fetches some subresources in a
// mode where it attaches no credentials, so the prompt returns forever; and
// ingress-nginx strips `Authorization` when it performs basic auth itself. So:
// a form that takes the token once, and a session cookie afterwards. There are
// no subresources at all either — the stylesheet is inline — which is the other
// half of not reproducing that problem.
//
// Server-rendered, no build step, no client JavaScript. A window, not an
// application: nothing here writes to the diary.
import { randomBytes } from 'node:crypto';

const COOKIE = 'cbdh_staff';
const SESSION_TTL_MS = Number(process.env.STAFF_SESSION_TTL_MS || 12 * 60 * 60 * 1000);
/** How many journal lines one page shows, newest first. */
const JOURNAL_PAGE = Number(process.env.STAFF_JOURNAL_ROWS || 200);

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------

/**
 * Sessions in memory, which is the right lifetime for them: the process holds
 * no secret worth persisting, and a restart asking the coordinator for the
 * token again is the safe direction to fail in.
 */
export class StaffSessions {
  constructor(ttlMs = SESSION_TTL_MS, now = () => Date.now()) {
    this.ttlMs = ttlMs;
    this.now = now;
    this.byId = new Map();
  }

  open(label) {
    const id = randomBytes(32).toString('base64url');
    this.byId.set(id, { label, openedAt: this.now(), seenAt: this.now() });
    return id;
  }

  /** The session this id names, if it is still live. Sweeps what has expired. */
  get(id) {
    for (const [key, session] of this.byId) {
      if (this.now() - session.seenAt >= this.ttlMs) this.byId.delete(key);
    }
    if (!id) return null;
    const session = this.byId.get(id);
    if (!session) return null;
    session.seenAt = this.now();
    return session;
  }

  close(id) {
    return id ? this.byId.delete(id) : false;
  }
}

/** The cookie named `name`, or null. Values are base64url, so no decoding is needed. */
export function readCookie(header, name = COOKIE) {
  for (const part of String(header ?? '').split(';')) {
    const i = part.indexOf('=');
    if (i < 0) continue;
    if (part.slice(0, i).trim() === name) return part.slice(i + 1).trim() || null;
  }
  return null;
}

/**
 * `Secure` is set from what the connection actually was. Behind this host's
 * Caddy that is always `https`; on a developer's `http://localhost` it is not,
 * and a `Secure` cookie there would simply never come back.
 */
export function sessionCookie(id, { secure, ttlMs = SESSION_TTL_MS }) {
  const attributes = [`${COOKIE}=${id}`, 'Path=/', 'HttpOnly', 'SameSite=Strict', `Max-Age=${Math.floor(ttlMs / 1000)}`];
  if (secure) attributes.push('Secure');
  return attributes.join('; ');
}

export function clearedCookie({ secure }) {
  const attributes = [`${COOKIE}=`, 'Path=/', 'HttpOnly', 'SameSite=Strict', 'Max-Age=0'];
  if (secure) attributes.push('Secure');
  return attributes.join('; ');
}

export function isSecureRequest(req) {
  const forwarded = String(req.headers['x-forwarded-proto'] ?? '').split(',')[0].trim();
  return forwarded === 'https' || Boolean(req.socket?.encrypted);
}

// ---------------------------------------------------------------------------
// Reading the journal
// ---------------------------------------------------------------------------

export function esc(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}

/**
 * The digest the caller presented, from either shape the journal records it in:
 * an accepted call carries it under `attested`, a digest mismatch carries it in
 * the refusal's `detail` because at that point there is no attested identity to
 * record. Both are the same fact and the screen shows it in one column.
 */
export function presentedDigest(entry) {
  if (entry.attested?.evidence_digest) return entry.attested.evidence_digest;
  const observed = /observed (sha256\/[A-Za-z0-9_+/=-]+)/.exec(entry.detail ?? '');
  return observed ? observed[1] : null;
}

/**
 * What the attestation check decided, in the words a viewer needs — which is
 * not the same as the outcome. A call can be refused with the code verified
 * (`site_outside_grant`) or refused before the code was ever fetched (no
 * credential), and a screen that collapses those into "refused" hides the
 * mechanism it exists to show.
 */
export function attestationVerdict(entry) {
  if (entry.outcome === 'accepted') return { state: 'verified', text: 'verified' };
  const reason = String(entry.reason ?? '');
  if (reason.startsWith('attestation_')) return { state: 'refused', text: reason.replace('attestation_', '') };
  if (reason === 'site_outside_grant') return { state: 'verified', text: 'verified · the grant refused the site' };
  if (reason.startsWith('no_')) return { state: 'skipped', text: 'not reached — no credential' };
  return { state: 'skipped', text: '—' };
}

const shortDigest = (digest) => (digest ? `${String(digest).slice(0, 14)}…` : '—');

/** `2026-11-23T15:29:42.847Z` → `2026-11-23 15:29:42 UTC`, which is what a journal wants. */
export function stamp(iso) {
  const value = String(iso ?? '');
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})/.exec(value);
  return match ? `${match[1]} ${match[2]} UTC` : value || '—';
}

/** How overdue a recall was, phrased rather than printed as a bare number. */
export function overdue(appointment) {
  const days = appointment.days_overdue;
  if (days === null || days === undefined) return '—';
  const years = Math.floor(days / 365);
  return years >= 1 ? `${days} days · ${years}y` : `${days} days`;
}

// ---------------------------------------------------------------------------
// The pages
// ---------------------------------------------------------------------------

const STYLE = `
:root{--ink:#16232b;--dim:#5d7180;--line:#d9e2e8;--bg:#f5f8fa;--card:#fff;
--ok:#0f6f4a;--ok-bg:#e6f4ee;--no:#9a2b22;--no-bg:#fbeae8;--idle:#5d7180;--idle-bg:#eef2f5;--accent:#1f5f8b}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{background:var(--card);border-bottom:1px solid var(--line)}
.bar{max-width:1320px;margin:0 auto;padding:18px 24px;display:flex;align-items:baseline;gap:16px;flex-wrap:wrap}
.bar h1{margin:0;font-size:19px;letter-spacing:-.01em}
.bar .sub{color:var(--dim);font-size:14px}
.bar form{margin:0 0 0 auto}
nav{max-width:1320px;margin:0 auto;padding:0 24px;display:flex;gap:4px}
nav a{padding:10px 16px;border-bottom:2px solid transparent;color:var(--dim);text-decoration:none;font-size:15px}
nav a.on{color:var(--accent);border-bottom-color:var(--accent);font-weight:600}
nav a:hover{color:var(--ink)}
main{max-width:1320px;margin:0 auto;padding:24px}
h2{font-size:17px;margin:0 0 4px}
.count{color:var(--dim);font-size:14px;margin:0 0 18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:14.5px}
th{text-align:left;font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);
font-weight:600;padding:11px 14px;border-bottom:1px solid var(--line);background:#fafcfd;white-space:nowrap}
td{padding:12px 14px;border-bottom:1px solid #eef2f5;vertical-align:top}
tr:last-child td{border-bottom:0}
tbody tr.refused{background:var(--no-bg)}
.who{font-weight:600}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
.muted{color:var(--dim)}
.nw{white-space:nowrap}
td.when{white-space:nowrap;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;color:var(--dim)}
.wrap{max-width:26ch;overflow-wrap:anywhere}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12.5px;font-weight:600;white-space:nowrap}
.pill.ok{background:var(--ok-bg);color:var(--ok)}
.pill.no{background:var(--no-bg);color:var(--no)}
.pill.idle{background:var(--idle-bg);color:var(--idle)}
.note{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--accent);
border-radius:10px;padding:16px 20px;margin:22px 0 0;font-size:14.5px;color:#2d4250}
.note h3{margin:0 0 8px;font-size:14px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim)}
.note p{margin:0 0 9px}
.note p:last-child{margin:0}
.note strong{color:var(--ink)}
.empty{padding:28px;color:var(--dim);text-align:center}
.login{max-width:430px;margin:9vh auto;background:var(--card);border:1px solid var(--line);
border-radius:12px;padding:30px}
.login h1{margin:0 0 6px;font-size:20px}
.login p{margin:0 0 20px;color:var(--dim);font-size:14.5px}
label{display:block;font-size:13px;font-weight:600;margin-bottom:7px}
input[type=password]{width:100%;padding:11px 13px;border:1px solid var(--line);border-radius:7px;
font:14px ui-monospace,SFMono-Regular,Menlo,monospace;background:#fcfdfe}
input[type=password]:focus{outline:2px solid #bcd8ea;border-color:var(--accent)}
button{margin-top:16px;padding:11px 20px;border:0;border-radius:7px;background:var(--accent);color:#fff;
font:600 15px ui-sans-serif,system-ui,sans-serif;cursor:pointer}
button:hover{background:#17476a}
button.plain{background:0;color:var(--dim);padding:6px 0;margin:0;font-size:14px;text-decoration:underline}
button.plain:hover{color:var(--ink);background:0}
.err{background:var(--no-bg);color:var(--no);border-radius:7px;padding:11px 13px;margin:0 0 18px;font-size:14px}
footer{max-width:1320px;margin:0 auto;padding:22px 24px 40px;color:var(--dim);font-size:13px}
`;

function page({ title, body, nav = '', who = null }) {
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(title)} · Cypress Bay Digestive Health</title>
<style>${STYLE}</style></head>
<body>
<header>
 <div class="bar">
  <h1>Cypress Bay Digestive Health</h1>
  <span class="sub">Scheduling system</span>
  ${who ? `<form method="post" action="/staff/sign-out"><button class="plain" type="submit">Sign out ${esc(who)}</button></form>` : ''}
 </div>
 ${nav ? `<nav>${nav}</nav>` : ''}
</header>
<main>${body}</main>
${who ? `<footer>Read-only. Appointments are written by the clinic's scheduling tool and nothing on
this screen changes them.</footer>` : ''}
</body></html>
`;
}

const navFor = (here) => ['appointments', 'journal']
  .map((view) => `<a href="/staff/${view}"${view === here ? ' class="on"' : ''}>${view === 'appointments' ? 'Appointments' : 'Journal'}</a>`)
  .join('');

export function loginPage({ error = null } = {}) {
  return page({
    title: 'Sign in',
    body: `<div class="login">
<h1>Scheduling system</h1>
<p>For the practice's own coordinators. Paste the staff access token the practice issued you.</p>
${error ? `<div class="err">${esc(error)}</div>` : ''}
<form method="post" action="/staff/session">
 <label for="token">Staff access token</label>
 <input id="token" name="token" type="password" autocomplete="off" autofocus
        spellcheck="false" placeholder="&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;">
 <button type="submit">Sign in</button>
</form>
</div>`,
  });
}

export function appointmentsPage(appointments, session) {
  const rows = [...appointments].reverse().map((one) => `<tr>
 <td><span class="who">${esc(one.patient_name)}</span><br><span class="mono muted">${esc(one.mrn)}</span></td>
 <td>${esc(one.site)}</td>
 <td class="nw">${esc(overdue(one))}<br><span class="muted">due ${esc(one.due_date ?? '—')}</span></td>
 <td class="mono nw">${esc(one.appointment_id)}<br><span class="muted">${esc(one.appointment_date)} ${esc(one.appointment_time)}</span></td>
 <td class="nw">${esc(stamp(one.booked_at))}<br><span class="muted">${esc(one.outreach_channel)}</span></td>
 <td class="wrap">${esc(one.requested_by)}<br><span class="mono muted nw" title="${esc(one.accepted_from?.evidence_digest ?? '')}">${esc(shortDigest(one.accepted_from?.evidence_digest))}</span></td>
</tr>`).join('\n');

  return page({
    title: 'Appointments',
    who: session.label,
    nav: navFor('appointments'),
    body: `<h2>Surveillance recalls booked</h2>
<p class="count">${appointments.length} appointment${appointments.length === 1 ? '' : 's'} in the diary, newest first.</p>
<div class="card">${appointments.length === 0 ? '<p class="empty">Nothing booked yet.</p>' : `<table>
<thead><tr><th>Patient</th><th>Site</th><th>Overdue</th><th>Appointment</th><th>Booked</th><th>Requested by</th></tr></thead>
<tbody>
${rows}
</tbody></table>`}</div>
<div class="note">
 <h3>Where these came from</h3>
 <p>Each row was written by one call from the confidential deployment the practice approved, over
 the single tool it was granted. The digest beside “requested by” is the code that was running
 when the clinic accepted the call &mdash; the <a href="/staff/journal">Journal</a> has the whole
 verdict.</p>
 <p>Nothing on this system can read a patient record: the deployment's token carries one write
 tool and no read scope at all.</p>
</div>`,
  });
}

export function journalPage(entries, session) {
  const shown = [...entries].reverse().slice(0, JOURNAL_PAGE);
  const refusals = entries.filter((one) => one.outcome === 'refused').length;
  const rows = shown.map((one) => {
    const verdict = attestationVerdict(one);
    const pill = verdict.state === 'verified' ? 'ok' : verdict.state === 'refused' ? 'no' : 'idle';
    // An attestation refusal happens before the JSON-RPC frame is read, so the
    // journal records no tool and no path for it; the route is nonetheless the
    // only one attestation is performed on, and leaving the cell empty reads as
    // a gap in the record rather than as the stage the refusal came at.
    const what = one.tool
      ? `${one.tool}${one.result ? ` · ${one.result}` : ''}`
      : (one.path ?? (one.approvedDigest ? '/mcp' : '—'));
    const digest = presentedDigest(one);
    const approved = one.approvedDigest && one.approvedDigest !== digest ? one.approvedDigest : null;
    return `<tr${one.outcome === 'refused' ? ' class="refused"' : ''}>
 <td class="when">${esc(stamp(one.at))}</td>
 <td class="nw"><span class="pill ${one.outcome === 'accepted' ? 'ok' : 'no'}">${esc(one.outcome)}</span>${one.reason ? `<br><span class="mono muted">${esc(one.reason)}</span>` : ''}</td>
 <td><span class="nw">${esc(what)}</span>${one.grant ? `<br><span class="muted">via ${esc(one.grant)}</span>` : ''}${one.recall_id ? `<br><span class="mono muted nw">${esc(one.recall_id)}${one.appointment_id ? ` → ${esc(one.appointment_id)}` : ''}</span>` : ''}${one.site ? `<br><span class="muted">${esc(one.site)}</span>` : ''}</td>
 <td class="nw"><span class="pill ${pill}">${esc(verdict.text)}</span></td>
 <td class="mono nw" title="${esc(digest ?? '')}">${esc(shortDigest(digest))}${approved ? `<br><span class="muted" title="${esc(approved)}">approved ${esc(shortDigest(approved))}</span>` : ''}</td>
</tr>`;
  }).join('\n');

  return page({
    title: 'Journal',
    who: session.label,
    nav: navFor('journal'),
    body: `<h2>Every call to this system</h2>
<p class="count">${entries.length} entr${entries.length === 1 ? 'y' : 'ies'}, ${refusals} of them refused${entries.length > shown.length ? ` &mdash; the newest ${shown.length} are shown` : ''}. Append-only, newest first.</p>
<div class="card">${shown.length === 0 ? '<p class="empty">No calls recorded yet.</p>' : `<table>
<thead><tr><th>When</th><th>Outcome</th><th>Call</th><th>Attestation</th><th>Digest presented</th></tr></thead>
<tbody>
${rows}
</tbody></table>`}</div>
<div class="note">
 <h3>What this journal shows, and what it does not</h3>
 <p>Outbound traffic from the deployment carries only the action the practice authorised, for the
 patient it authorised, and only from code the practice verified. <strong>Exactly how far “code
 the practice verified” reaches:</strong> the check binds the approved code to the
 <strong>hostname</strong> the clinic calls back. It is the <strong>token</strong> that binds the
 individual call &mdash; so anyone holding that token passes the code check while the deployment
 is healthy.</p>
 <p>The code is re-checked <strong>once per 60-second window</strong>, not on every call. A change
 of code is caught within the minute; it is not caught on the frame that changes.</p>
 <p>And the deployment's outbound destinations are <strong>not</strong> all part of its attested
 configuration today: the tool server it calls is registered inside the deployment's own database,
 which the signed snapshot does not cover. That is a known gap with a known fix, and until the fix
 ships this receiving end is where that last channel is constrained &mdash; by the grant that names
 one caller, one tool and six sites.</p>
</div>`,
  });
}
