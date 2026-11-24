#!/usr/bin/env node
// Give each clinic a marketplace persona that can actually sign in, so the
// withdrawal beat is performed by the data's owner on the owner's own screen.
//
//   MP_BASE=https://marketplace.demo.superprotocol.dev \
//   CLINIC_PASS_DIR=. node clinic-admins.js            # sign up, then print the SQL
//   … node clinic-admins.js --verify                   # sign in as each, list its orgs
//
// WHY A PERSONA IS NEEDED AT ALL. The catalogue seeder creates each publishing
// organisation with one member, `owner@<slug>.seed.local`, which is a synthetic
// session user: it has no password and nobody can sign in as it. So the three
// clinics own their cards and no human can act for them — which is fine until
// the demonstration needs a clinic to WITHDRAW a grant, and "the owner controls
// access" is the one claim that has to be performed rather than described.
//
// WHY THE MEMBERSHIP IS A PRINTED STATEMENT AND NOT AN API CALL. Inviting
// somebody into an organisation requires being an `admin` of it
// (`organizations.controller.ts`, `require(user.id, organizationId, 'admin')`),
// and the only member of a clinic organisation cannot sign in. There is no
// bootstrap path. So the script does the part the API allows — creating the
// users, with their own passwords, through the ordinary sign-up — and prints the
// one row per clinic that has to be written on the marketplace host.
//
// That row is printed rather than executed because it is a privileged write on
// somebody else's database from a script on a different host, and because the
// operator should see exactly what is being added before adding it. Take a
// backup first; the README says so and names the one this was tested against.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const BASE = (process.env.MP_BASE || 'https://marketplace.demo.superprotocol.dev').replace(/\/$/, '');
const PASS_DIR = process.env.CLINIC_PASS_DIR || __dirname;
const VERIFY = process.argv.includes('--verify');

/** The three clinics, as `[organisation slug, persona]`. The slug is what
 *  `catalog.yaml` publishes under and what the printed statement resolves; the
 *  persona is the address that appears on screen. */
const CLINICS = [
  ['cypress-bay-digestive', 'cypress-bay-admin@local.test', 'Cypress Bay Digestive Health'],
  ['harbor-point-gastro', 'harbor-point-admin@local.test', 'Harbor Point Gastroenterology'],
  ['ridgeline-endoscopy', 'ridgeline-admin@local.test', 'Ridgeline Endoscopy Partners'],
];

const say = (...parts) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...parts);

/** A password the operator can read off a file and type, kept out of the
 *  repository by `*.password` in `stands/latica/.gitignore`. Generated once and
 *  reused on a re-run, so re-running this script does not lock anybody out. */
function password(persona) {
  const file = path.join(PASS_DIR, `${persona.split('@')[0]}.password`);
  if (fs.existsSync(file) && fs.readFileSync(file, 'utf8').trim().length >= 16) {
    return [fs.readFileSync(file, 'utf8').trim(), file, false];
  }
  const alphabet = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  const value = [...crypto.randomBytes(28)].map((byte) => alphabet[byte % alphabet.length]).join('');
  fs.writeFileSync(file, value, { mode: 0o600 });
  return [value, file, true];
}

async function auth(route, body) {
  const response = await fetch(`${BASE}/api/auth/${route}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Origin: BASE },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => null);
  return [response.status, payload];
}

async function organizationsOf(token) {
  const response = await fetch(`${BASE}/api/v1/organizations`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) return null;
  return (payload.items || payload || []).map((organization) => `${organization.slug}:${organization.role}`);
}

(async () => {
  const statements = [];
  for (const [slug, persona, display] of CLINICS) {
    const [secret, file, fresh] = password(persona);
    let [status, body] = await auth('sign-up/email', {
      email: persona,
      password: secret,
      name: `${display} administrator`,
    });
    if (status >= 300) {
      // Already there, which is the re-run case. Signing in proves the password
      // on file is still the right one, which is the thing worth knowing.
      [status, body] = await auth('sign-in/email', { email: persona, password: secret });
      if (status >= 300) {
        say(`  ${persona}: EXISTS and the password in ${path.basename(file)} does not work (${status})`);
        process.exitCode = 1;
        continue;
      }
      say(`  ${persona}: already signed up, password on file still works`);
    } else {
      say(`  ${persona}: signed up${fresh ? `, password written to ${path.basename(file)}` : ''}`);
    }

    const token = body && body.token;
    const userId = body && body.user && body.user.id;
    if (!token || !userId) {
      say(`  ${persona}: no session came back — cannot report its id`);
      process.exitCode = 1;
      continue;
    }
    const mine = await organizationsOf(token);
    say(`     id ${userId}; member of ${JSON.stringify(mine)}`);
    const belongs = (mine || []).some((entry) => entry.startsWith(`${slug}:`));
    if (VERIFY) {
      if (belongs) say(`     ${slug}: IN PLACE`);
      else { say(`     ${slug}: NOT a member — the statement below has not been run`); process.exitCode = 1; }
    }
    if (!belongs) {
      statements.push(
        `INSERT INTO organization_members ("organizationId", "userId", email, "displayName", role)\n`
          + `SELECT id, '${userId}', '${persona}', '${display} administrator', 'admin'\n`
          + `  FROM organizations WHERE slug = '${slug}'\n`
          + `  ON CONFLICT DO NOTHING;`,
      );
    }
  }

  if (statements.length === 0) {
    say(VERIFY ? 'every persona is an admin of its clinic' : 'nothing left to add');
    return;
  }
  console.log('');
  say('Back up the marketplace database first, then run these on the marketplace host:');
  console.log('');
  console.log('  docker exec -i marketplace-postgres-1 pg_dump -U marketplace -d swarm_marketplace > /tmp/b.sql');
  console.log('  sudo mv /tmp/b.sql /opt/marketplace/backup-$(date -u +%Y%m%dT%H%M%SZ)-pre-clinic-admins.sql');
  console.log('');
  for (const statement of statements) {
    console.log(`  docker exec -i marketplace-postgres-1 psql -U marketplace -d swarm_marketplace -c "$(cat <<'SQL'\n${statement}\nSQL\n)"`);
    console.log('');
  }
  say('then re-run with --verify');
})().catch((error) => {
  console.log('FAILED', error.message);
  process.exit(1);
});
