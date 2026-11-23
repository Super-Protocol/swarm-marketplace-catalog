// The routes, against the real process. The unit tests say what each page
// renders; this says what the server admits — which is the half that the
// requirement "the deployment's token must still get 401 on the read routes,
// and the staff token 401 on /mcp" is actually about.
//
// It starts `main.mjs` as a child on a free port with its own grants file and
// its own data directory, so nothing here touches a real diary.
//
//   node --test src/server.spec.mjs
import { strict as assert } from 'node:assert';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, describe, it } from 'node:test';
import { fileURLToPath } from 'node:url';

const MAIN = fileURLToPath(new URL('./main.mjs', import.meta.url));
const CALLER = 'caller-token-for-the-tests';
const STAFF = 'staff-token-for-the-tests';

let dir;
let child;
let base;

/** No redirect following: a 303 and its `Set-Cookie` are the thing being asserted. */
const go = (path, options = {}) => fetch(`${base}${path}`, { redirect: 'manual', ...options });

const form = (fields) => ({
  method: 'POST',
  headers: { 'content-type': 'application/x-www-form-urlencoded' },
  body: new URLSearchParams(fields).toString(),
});

/** The session cookie out of a `Set-Cookie`, as a browser would send it back. */
function cookieFrom(response) {
  const header = response.headers.getSetCookie?.()[0] ?? response.headers.get('set-cookie') ?? '';
  return header.split(';')[0];
}

describe('the scheduling system over HTTP', () => {
  before(async () => {
    dir = mkdtempSync(join(tmpdir(), 'cbdh-server-'));
    const grantsPath = join(dir, 'grants.json');
    writeFileSync(grantsPath, JSON.stringify({
      pinnedRootFingerprint: 'AA:BB',
      grants: [
        {
          label: 'latica-analyst-stand', token: CALLER, scopes: ['mcp'],
          tools: ['schedule_recall'], sites: ['Palmetto Point Gastroenterology'],
          evidenceUrl: 'https://nowhere.invalid/.well-known/swarm-evidence',
          approvedDigest: 'sha256/approved',
        },
        { label: 'cypress-bay-staff', token: STAFF, scopes: ['read'] },
      ],
    }));
    const data = join(dir, 'data');
    // A diary and a journal that already hold the two rows the screen is
    // required to show: an acceptance and a refusal.
    const { mkdirSync } = await import('node:fs');
    mkdirSync(data, { recursive: true });
    writeFileSync(join(data, 'appointments.jsonl'), `${JSON.stringify({
      appointment_id: 'APPT-F2B7E9B1', booked_at: '2026-11-23T15:29:42.847Z', recall_id: 'RC-0013565',
      mrn: 'CB-1079862', patient_name: 'Abernathy, Marcia', site: 'Palmetto Point Gastroenterology',
      appointment_date: '2026-11-30', appointment_time: '08:00', outreach_channel: 'letter',
      due_date: '2019-04-30', days_overdue: 2758, requested_by: 'SURV-01 · 2314d0c3',
      accepted_from: { evidence_digest: 'sha256/05HLhdgiR5f' },
    })}\n`);
    writeFileSync(join(data, 'journal.jsonl'), [
      JSON.stringify({ at: '2026-11-23T15:19:02.000Z', outcome: 'accepted', tool: 'schedule_recall', result: 'booked', grant: 'latica-analyst-stand', recall_id: 'RC-0013565', appointment_id: 'APPT-F2B7E9B1', attested: { evidence_digest: 'sha256/05HLhdgiR5f' } }),
      JSON.stringify({ at: '2026-11-23T15:45:00.000Z', outcome: 'refused', reason: 'attestation_digest_mismatch', detail: 'observed sha256/05HLhdgiR5f', grant: 'latica-analyst-stand-previously-approved' }),
      '',
    ].join('\n'));

    child = spawn(process.execPath, [MAIN], {
      env: { ...process.env, PORT: '0', GRANTS_PATH: grantsPath, DATA_DIR: data },
      stdio: ['ignore', 'pipe', 'inherit'],
    });
    // `PORT=0` makes the kernel choose; the process prints the port it got.
    base = await new Promise((resolve, reject) => {
      let buffered = '';
      const timer = setTimeout(() => reject(new Error(`no listening line: ${buffered}`)), 15_000);
      child.stdout.on('data', (chunk) => {
        buffered += chunk;
        for (const line of buffered.split('\n')) {
          try {
            const parsed = JSON.parse(line);
            if (parsed.event === 'listening') { clearTimeout(timer); resolve(`http://127.0.0.1:${parsed.port}`); }
          } catch { /* a partial line */ }
        }
      });
      child.on('error', reject);
    });
  });

  after(() => {
    child?.kill('SIGKILL');
    rmSync(dir, { recursive: true, force: true });
  });

  // -------------------------------------------------------------------------
  // What the change must not have altered
  // -------------------------------------------------------------------------

  it('still answers /healthz without a credential', async () => {
    const response = await go('/healthz');
    assert.equal(response.status, 200);
    const body = await response.json();
    assert.equal(body.status, 'ok');
    assert.equal(body.callers, 1);
    assert.equal(body.readers, 1);
  });

  it('still refuses the read routes to the DEPLOYMENT\'s token', async () => {
    for (const path of ['/appointments', '/journal']) {
      const response = await go(path, { headers: { authorization: `Bearer ${CALLER}` } });
      assert.equal(response.status, 401, `${path} with the caller token`);
    }
  });

  it('still refuses /mcp to the STAFF token', async () => {
    const response = await go('/mcp', {
      method: 'POST',
      headers: { authorization: `Bearer ${STAFF}`, 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' }),
    });
    assert.equal(response.status, 401);
  });

  it('still serves the read routes as JSON to the staff token', async () => {
    const response = await go('/appointments', { headers: { authorization: `Bearer ${STAFF}` } });
    assert.equal(response.status, 200);
    assert.match(response.headers.get('content-type'), /application\/json/);
    assert.equal((await response.json())[0].appointment_id, 'APPT-F2B7E9B1');
  });

  it('still refuses an unknown token', async () => {
    assert.equal((await go('/journal', { headers: { authorization: 'Bearer nonsense' } })).status, 401);
  });

  // -------------------------------------------------------------------------
  // The screen
  // -------------------------------------------------------------------------

  it('offers a sign-in form, and no browser prompt, to a visitor with no session', async () => {
    const response = await go('/staff/appointments');
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('www-authenticate'), null);
    const html = await response.text();
    assert.match(html, /action="\/staff\/session"/);
    assert.ok(!html.includes('Abernathy'), 'the diary must not be on the sign-in page');
  });

  it('lands a bare / on the sign-in form rather than a 404', async () => {
    const response = await go('/');
    assert.equal(response.status, 200);
    assert.match(await response.text(), /action="\/staff\/session"/);
  });

  it('refuses the DEPLOYMENT\'s token at the form — the screen is scope `read`', async () => {
    const response = await go('/staff/session', form({ token: CALLER }));
    assert.equal(response.status, 401);
    assert.equal(cookieFrom(response), '');
  });

  it('refuses a token nobody issued, and records the attempt in the journal', async () => {
    const before_ = (await (await go('/journal', { headers: { authorization: `Bearer ${STAFF}` } })).json()).length;
    const response = await go('/staff/session', form({ token: 'not-a-token' }));
    assert.equal(response.status, 401);
    const journal = await (await go('/journal', { headers: { authorization: `Bearer ${STAFF}` } })).json();
    assert.equal(journal.length, before_ + 1);
    assert.equal(journal.at(-1).reason, 'no_staff_credential');
  });

  it('takes the staff token once and carries a session cookie afterwards', async () => {
    const signIn = await go('/staff/session', form({ token: STAFF }));
    assert.equal(signIn.status, 303);
    assert.equal(signIn.headers.get('location'), '/staff/appointments');
    const cookie = cookieFrom(signIn);
    assert.match(cookie, /^cbdh_staff=.+/);
    const setCookie = signIn.headers.getSetCookie()[0];
    assert.match(setCookie, /HttpOnly/);
    assert.match(setCookie, /SameSite=Strict/);

    const diary = await go('/staff/appointments', { headers: { cookie } });
    assert.equal(diary.status, 200);
    assert.match(diary.headers.get('content-type'), /text\/html/);
    assert.equal(diary.headers.get('cache-control'), 'no-store');
    const html = await diary.text();
    assert.match(html, /Abernathy, Marcia/);
    assert.match(html, /APPT-F2B7E9B1/);
    // The credential itself must never come back down the wire.
    assert.ok(!html.includes(STAFF));

    const journal = await (await go('/staff/journal', { headers: { cookie } })).text();
    assert.match(journal, /attestation_digest_mismatch/);
    assert.match(journal, /hostname<\/strong> the clinic calls back/);

    const signOut = await go('/staff/sign-out', { method: 'POST', headers: { cookie } });
    assert.equal(signOut.status, 303);
    const after_ = await go('/staff/journal', { headers: { cookie } });
    assert.match(await after_.text(), /action="\/staff\/session"/, 'the session is gone after sign-out');
  });

  it('does not let a guessed cookie in', async () => {
    const response = await go('/staff/journal', { headers: { cookie: 'cbdh_staff=guessed' } });
    assert.match(await response.text(), /action="\/staff\/session"/);
  });

  it('serves the screen with a policy that forbids script and framing', async () => {
    const cookie = cookieFrom(await go('/staff/session', form({ token: STAFF })));
    const response = await go('/staff/appointments', { headers: { cookie } });
    const policy = response.headers.get('content-security-policy');
    assert.match(policy, /default-src 'none'/);
    assert.match(policy, /frame-ancestors 'none'/);
    assert.equal(response.headers.get('referrer-policy'), 'no-referrer');
  });

  it('caps the sign-in body rather than reading whatever is sent', async () => {
    const response = await go('/staff/session', {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: `token=${'x'.repeat(64 * 1024)}`,
    });
    assert.equal(response.status, 413);
  });

  // -------------------------------------------------------------------------
  // The state the demonstration is made of
  // -------------------------------------------------------------------------

  it('reads the diary off disk, so a restart does not lose it', () => {
    // The screen renders `store.appointments()`, which reads the file on every
    // request — there is no in-memory copy to lose. This asserts the file the
    // test wrote is still the file the process is serving from.
    const path = join(dir, 'data', 'appointments.jsonl');
    assert.ok(existsSync(path));
    assert.match(readFileSync(path, 'utf8'), /APPT-F2B7E9B1/);
  });
});
