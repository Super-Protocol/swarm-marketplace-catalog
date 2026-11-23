// The screen, in pieces: the session, the cookie, and what each page is
// required to say. The copy assertions are not decoration — the wording about
// what attestation binds is the thing this project is most able to get wrong,
// and a test is how it stops being optional.
//
//   node --test src/staff.spec.mjs
import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';
import {
  StaffSessions, appointmentsPage, attestationVerdict, clearedCookie, esc, isSecureRequest,
  journalPage, loginPage, overdue, presentedDigest, readCookie, sessionCookie, stamp,
} from './staff.mjs';

const SESSION = { label: 'cypress-bay-staff' };

const APPOINTMENT = {
  appointment_id: 'APPT-F2B7E9B1',
  booked_at: '2026-11-23T15:29:42.847Z',
  recall_id: 'RC-0013565',
  mrn: 'CB-1079862',
  patient_name: 'Abernathy, Marcia',
  site: 'Palmetto Point Gastroenterology',
  appointment_date: '2026-11-30',
  appointment_time: '08:00',
  outreach_channel: 'letter',
  due_date: '2019-04-30',
  days_overdue: 2758,
  requested_by: 'SURV-01 · 2314d0c3',
  accepted_from: { evidence_digest: 'sha256/05HLhdgiR5fOaf50fBHQhRUF61GdXh4t9NL602zAvzo' },
};

const ACCEPTED = {
  at: '2026-11-23T15:19:02.000Z', outcome: 'accepted', tool: 'schedule_recall', result: 'booked',
  grant: 'latica-analyst-stand', recall_id: 'RC-0013565', appointment_id: 'APPT-F2B7E9B1',
  attested: { hostname: 'analyst-stand.conf-apps.superprotocol.dev', evidence_digest: 'sha256/05HLhdgiR5f' },
};

const REFUSED = {
  at: '2026-11-23T15:45:00.000Z', outcome: 'refused', reason: 'attestation_digest_mismatch',
  detail: 'observed sha256/05HLhdgiR5fOaf50fBHQhRUF61GdXh4t9NL602zAvzo',
  grant: 'latica-analyst-stand-previously-approved',
};

describe('a staff session', () => {
  it('hands out an unguessable id and remembers who holds it', () => {
    const sessions = new StaffSessions();
    const first = sessions.open('cypress-bay-staff');
    const second = sessions.open('cypress-bay-staff');
    assert.notEqual(first, second);
    assert.ok(first.length >= 40, `an id of ${first.length} characters is too short`);
    assert.equal(sessions.get(first).label, 'cypress-bay-staff');
  });

  it('does not recognise an id it never issued', () => {
    const sessions = new StaffSessions();
    sessions.open('cypress-bay-staff');
    assert.equal(sessions.get('made-up'), null);
    assert.equal(sessions.get(null), null);
  });

  it('expires on idleness, and the clock is the only thing that decides', () => {
    let now = 1_000_000;
    const sessions = new StaffSessions(60_000, () => now);
    const id = sessions.open('cypress-bay-staff');
    now += 59_000;
    assert.ok(sessions.get(id), 'still inside the window');
    // The read above moved `seenAt`, so the session lives from then, not from
    // when it was opened — an idle timeout, not a fixed lifetime.
    now += 59_000;
    assert.ok(sessions.get(id), 'the read refreshed it');
    now += 61_000;
    assert.equal(sessions.get(id), null, 'idle past the ttl');
  });

  it('forgets a session on sign-out', () => {
    const sessions = new StaffSessions();
    const id = sessions.open('cypress-bay-staff');
    assert.equal(sessions.close(id), true);
    assert.equal(sessions.get(id), null);
  });
});

describe('the session cookie', () => {
  it('is HttpOnly and SameSite=Strict, and Secure only on a TLS request', () => {
    const over = sessionCookie('abc', { secure: true });
    assert.match(over, /^cbdh_staff=abc;/);
    assert.match(over, /HttpOnly/);
    assert.match(over, /SameSite=Strict/);
    assert.match(over, /Secure/);
    assert.ok(!sessionCookie('abc', { secure: false }).includes('Secure'));
  });

  it('is cleared with Max-Age=0 and no value', () => {
    assert.match(clearedCookie({ secure: true }), /^cbdh_staff=;.*Max-Age=0/);
  });

  it('is found among the other cookies a browser sends', () => {
    assert.equal(readCookie('_ga=1; cbdh_staff=tok-en_9; other=2'), 'tok-en_9');
    assert.equal(readCookie('_ga=1; other=2'), null);
    assert.equal(readCookie(undefined), null);
    assert.equal(readCookie('cbdh_staff='), null);
  });

  it('reads the proxy\'s protocol, because the TLS ends at Caddy', () => {
    assert.equal(isSecureRequest({ headers: { 'x-forwarded-proto': 'https' } }), true);
    assert.equal(isSecureRequest({ headers: { 'x-forwarded-proto': 'https, http' } }), true);
    assert.equal(isSecureRequest({ headers: { 'x-forwarded-proto': 'http' } }), false);
    assert.equal(isSecureRequest({ headers: {} }), false);
    assert.equal(isSecureRequest({ headers: {}, socket: { encrypted: true } }), true);
  });
});

describe('reading a journal entry', () => {
  it('finds the presented digest in both shapes the journal records it in', () => {
    assert.equal(presentedDigest(ACCEPTED), 'sha256/05HLhdgiR5f');
    assert.equal(presentedDigest(REFUSED), 'sha256/05HLhdgiR5fOaf50fBHQhRUF61GdXh4t9NL602zAvzo');
    assert.equal(presentedDigest({ outcome: 'refused', reason: 'no_credential' }), null);
  });

  it('keeps the attestation verdict apart from the outcome', () => {
    // A refusal with the code verified and a refusal before the code was ever
    // fetched are different facts, and the screen exists to show which.
    assert.equal(attestationVerdict(ACCEPTED).state, 'verified');
    assert.equal(attestationVerdict(REFUSED).state, 'refused');
    assert.equal(attestationVerdict(REFUSED).text, 'digest_mismatch');
    assert.equal(attestationVerdict({ outcome: 'refused', reason: 'site_outside_grant' }).state, 'verified');
    assert.equal(attestationVerdict({ outcome: 'refused', reason: 'no_credential' }).state, 'skipped');
    assert.equal(attestationVerdict({ outcome: 'refused', reason: 'no_read_credential' }).state, 'skipped');
  });

  it('prints a timestamp a journal can be read from, and says UTC', () => {
    assert.equal(stamp('2026-11-23T15:29:42.847Z'), '2026-11-23 15:29:42 UTC');
    assert.equal(stamp(undefined), '—');
  });

  it('phrases how overdue a recall was', () => {
    assert.equal(overdue({ days_overdue: 2758 }), '2758 days · 7y');
    assert.equal(overdue({ days_overdue: 10 }), '10 days');
    assert.equal(overdue({}), '—');
  });
});

describe('escaping', () => {
  it('neutralises every character that could close a tag or an attribute', () => {
    assert.equal(esc(`<img src=x onerror="a">&'`), '&lt;img src=x onerror=&quot;a&quot;&gt;&amp;&#39;');
    assert.equal(esc(null), '');
  });
});

describe('the sign-in page', () => {
  it('is a form, not a browser prompt', () => {
    const html = loginPage();
    // HTTP Basic is what this must not be: an SPA's subresources are fetched
    // with no credentials so the prompt never ends, and ingress-nginx strips
    // `Authorization` when it performs basic auth itself.
    assert.ok(!/WWW-Authenticate/i.test(html));
    assert.match(html, /<form method="post" action="\/staff\/session">/);
    assert.match(html, /name="token" type="password"/);
  });

  it('shows a refusal without echoing what was typed', () => {
    const html = loginPage({ error: 'That is not a token this practice issued for reading.' });
    assert.match(html, /not a token this practice issued/);
  });
});

describe('the appointments page', () => {
  const html = appointmentsPage([APPOINTMENT], SESSION);

  it('shows the patient, the site, how overdue, the appointment and who asked', () => {
    assert.match(html, /Abernathy, Marcia/);
    assert.match(html, /CB-1079862/);
    assert.match(html, /Palmetto Point Gastroenterology/);
    assert.match(html, /2758 days/);
    assert.match(html, /APPT-F2B7E9B1/);
    assert.match(html, /2026-11-23 15:29:42 UTC/);
    assert.match(html, /SURV-01/);
  });

  it('names the digest the booking was accepted from', () => {
    assert.match(html, /sha256\/05HLhdgiR5f/);
  });

  it('escapes what the caller supplied', () => {
    const hostile = appointmentsPage([{ ...APPOINTMENT, patient_name: '<script>alert(1)</script>' }], SESSION);
    assert.ok(!hostile.includes('<script>alert(1)</script>'));
    assert.match(hostile, /&lt;script&gt;/);
  });

  it('says so rather than breaking when the diary is empty', () => {
    assert.match(appointmentsPage([], SESSION), /Nothing booked yet/);
  });
});

describe('the journal page', () => {
  const html = journalPage([ACCEPTED, REFUSED], SESSION);

  it('shows a refusal, not only the acceptances', () => {
    assert.match(html, /attestation_digest_mismatch/);
    assert.match(html, /class="refused"/);
    assert.match(html, /1 of them refused/);
  });

  it('shows the verdict and the digest for each call', () => {
    assert.match(html, /schedule_recall/);
    assert.match(html, /latica-analyst-stand/);
    assert.match(html, /digest_mismatch/);
  });

  it('states what the attestation binds, and what only the token binds', () => {
    assert.match(html, /hostname<\/strong> the clinic calls back/);
    assert.match(html, /<strong>token<\/strong> that binds the\s+individual call/);
    assert.match(html, /anyone holding that token passes the code check/);
  });

  it('states that the code is re-checked once per window, not per call', () => {
    assert.match(html, /once per 60-second window<\/strong>, not on every call/);
  });

  it('does not claim every outbound destination is attested', () => {
    assert.match(html, /<strong>not<\/strong> all part of its attested\s+configuration today/);
    assert.ok(!/every outbound destination is (part of the )?attested/i.test(html.replace(/\s+/g, ' ')));
  });

  it('never says nothing leaves the perimeter', () => {
    // True until an autopilot writes a recall back, and false afterwards. The
    // stand's README forbids the phrase; so does this.
    assert.ok(!/nothing leaves/i.test(html));
  });
});
