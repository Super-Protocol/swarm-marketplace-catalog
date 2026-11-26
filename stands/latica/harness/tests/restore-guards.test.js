// The guards `restore-stand.js` answers BEFORE it opens a session, and the
// refusals it must never walk past.
//
//   node --test stands/latica/harness/tests/
//
// Every case here points `APP_BASE` at an address that does not resolve. That is
// the assertion: if the script reached the network, the message would be a
// connection failure, and these tests would see it instead of the refusal they
// expect. A precondition that is only checked after sign-in is a precondition
// that cannot be checked at all when the stand is the thing that is wrong.
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const SCRIPT = path.join(__dirname, '..', 'restore-stand.js');
const BACKUP = path.join(__dirname, '..', '..', 'configuration-backup');
/** Unroutable on purpose: this is how the tests prove nothing was dialled. */
const NOWHERE = 'https://restore-stand-test.invalid';

/** Run the script with a clean environment, and give back what it said. */
function run(env) {
  const result = spawnSync(process.execPath, [SCRIPT], {
    encoding: 'utf8',
    timeout: 30000,
    env: {
      PATH: process.env.PATH,
      HOME: process.env.HOME,
      IN: BACKUP,
      ...env,
    },
  });
  return { status: result.status, out: `${result.stdout ?? ''}${result.stderr ?? ''}` };
}

/** A copy of the export, so a test can break one file without touching the
 *  real backup. */
function copyBackup(mutate) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'restore-guard-'));
  for (const name of fs.readdirSync(BACKUP)) {
    fs.copyFileSync(path.join(BACKUP, name), path.join(dir, name));
  }
  mutate(dir);
  return dir;
}

test('no APP_BASE is refused', () => {
  const { status, out } = run({});
  assert.equal(status, 1);
  assert.match(out, /APP_BASE is required/);
});

test('CONFIRM_TARGET has to repeat APP_BASE exactly', () => {
  const { status, out } = run({ APP_BASE: NOWHERE, CONFIRM_TARGET: `${NOWHERE}/` });
  assert.equal(status, 1);
  assert.match(out, /CONFIRM_TARGET must repeat APP_BASE exactly/);
});

test('the live stand is refused by name, even with CONFIRM_TARGET agreeing', () => {
  const live = 'https://analyst-stand.conf-apps.superprotocol.dev';
  const { status, out } = run({ APP_BASE: live, CONFIRM_TARGET: live });
  assert.equal(status, 1);
  assert.match(out, /configured by hand; refusing/);
});

test('ALLOW_LIVE_STAND only counts when it says yes', () => {
  const live = 'https://analyst-stand.conf-apps.superprotocol.dev';
  const { out } = run({ APP_BASE: live, CONFIRM_TARGET: live, ALLOW_LIVE_STAND: 'true' });
  assert.match(out, /configured by hand; refusing/);
});

test('a missing file in the export stops the run before anything is dialled', () => {
  const dir = copyBackup((into) => fs.rmSync(path.join(into, 'dashboards.json')));
  const { status, out } = run({ APP_BASE: NOWHERE, CONFIRM_TARGET: NOWHERE, IN: dir });
  assert.equal(status, 1);
  assert.match(out, /dashboards\.json is missing/);
  assert.match(out, /nothing has been changed/);
  assert.ok(!/fetch failed|ENOTFOUND|getaddrinfo/i.test(out), 'it must not have tried to sign in');
});

test('a missing semantic model file is named with the tenant it belongs to', () => {
  const dir = copyBackup((into) => fs.rmSync(path.join(into, 'semantic-model.cypress-bay-digestive-health.json')));
  const { status, out } = run({ APP_BASE: NOWHERE, CONFIRM_TARGET: NOWHERE, IN: dir });
  assert.equal(status, 1);
  assert.match(out, /semantic-model\.cypress-bay-digestive-health\.json is missing/);
  assert.match(out, /Cypress Bay Digestive Health/);
});

test('a manifest that disagrees with its own files is a truncated backup, and is refused offline', () => {
  const dir = copyBackup((into) => {
    const items = JSON.parse(fs.readFileSync(path.join(into, 'library-items.json'), 'utf8'));
    fs.writeFileSync(path.join(into, 'library-items.json'), JSON.stringify(items.slice(0, 9)));
  });
  const { status, out } = run({ APP_BASE: NOWHERE, CONFIRM_TARGET: NOWHERE, IN: dir });
  assert.equal(status, 1);
  assert.match(out, /the manifest counts 15 libraryItems and the files hold 9/);
  assert.ok(!/fetch failed|ENOTFOUND|getaddrinfo/i.test(out), 'it must not have tried to sign in');
});

test('an export that counts autopilots without carrying them is refused', () => {
  const dir = copyBackup((into) => fs.rmSync(path.join(into, 'autopilots.json')));
  const { status, out } = run({ APP_BASE: NOWHERE, CONFIRM_TARGET: NOWHERE, IN: dir });
  assert.equal(status, 1);
  assert.match(out, /counts 2 autopilot\(s\) and autopilots\.json is missing/);
});

test('a complete export gets as far as the stand, and no further than a connection failure', () => {
  // The positive control for every test above: with the export intact, the
  // first thing that stops the run is the network — so the refusals the other
  // cases see really are the local checks and not an accident of ordering.
  const { status, out } = run({ APP_BASE: NOWHERE, CONFIRM_TARGET: NOWHERE, PASS_FILE: '/dev/null' });
  assert.equal(status, 1);
  assert.ok(/fetch failed|ENOTFOUND|getaddrinfo|EAI_AGAIN/i.test(out), out.slice(0, 400));
});
