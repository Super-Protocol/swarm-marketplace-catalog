// Load one generated clinic corpus into the stand's ClickHouse and create the
// reader account the dataset card's secrets name.
//
// Runs INSIDE marketplace-api-1: that container already holds the vault key and
// is one of the two hosts the ClickHouse on 10.10.1.11 answers, so the owner
// credential is decrypted and used where it lives and is never printed or
// carried out. The reader password arrives as an environment variable and is
// likewise never echoed.
//
// The reader is created with `SETTINGS readonly = 2`, NOT by attaching this
// server's shared `readonly` profile, which is `readonly = 1`. The difference
// decides whether the stand works at all: the analyst's ClickHouse adapter
// sends `max_execution_time` with every query and `max_result_rows` whenever a
// row limit applies, and `readonly = 1` refuses a settings change as well as a
// write — so every question comes back `Code: 164` while the gate still reports
// `allowed` and the card still looks connected (SUP-905).
const fs = require('node:fs');
const path = require('node:path');
const { createDecipheriv } = require('node:crypto');

const KEY = Buffer.from(env('VAULT_KEY'), 'base64');
const ROW = JSON.parse(env('STORAGE_ROW'));
const DIR = env('CORPUS_DIR');
const PREFIX = env('TABLE_PREFIX');
const READER = env('READER_USER');
const READER_PASSWORD = env('READER_PASSWORD');
const DATABASE = process.env.CH_DATABASE || 'marketplace';

function env(name) {
  const value = process.env[name];
  if (!value) throw new Error(`set ${name}`);
  return value;
}

// `READER_USER` and `TABLE_PREFIX` are interpolated into DDL as identifiers, not
// as literals, so they are checked rather than escaped: there is no quoting that
// makes an arbitrary string safe in that position, and both are ours to choose.
for (const [name, value] of [
  ['READER_USER', READER],
  ['TABLE_PREFIX', PREFIX],
]) {
  if (!/^[a-z][a-z0-9_]*$/.test(value)) {
    throw new Error(`${name} must be lowercase letters, digits and underscores — got "${value}"`);
  }
}

function decrypt(encoded) {
  const [version, iv, tag, ciphertext] = encoded.split(':');
  if (version !== 'v1') throw new Error('unexpected cipher version');
  const decipher = createDecipheriv('aes-256-gcm', KEY, Buffer.from(iv, 'base64'));
  decipher.setAuthTag(Buffer.from(tag, 'base64'));
  return Buffer.concat([decipher.update(Buffer.from(ciphertext, 'base64')), decipher.final()]).toString('utf8');
}

const OWNER_PASSWORD = decrypt(ROW.passwordCipher);
const URL = `http://${ROW.host}:${ROW.port}/`;

/** One statement per request: ClickHouse's HTTP interface runs exactly one, and a
 *  whole file comes back 400, which reads like a malformed statement and is not. */
async function run(sql, { user = ROW.username, password = OWNER_PASSWORD, body = null } = {}) {
  const response = await fetch(body === null ? URL : `${URL}?query=${encodeURIComponent(sql)}`, {
    method: 'POST',
    headers: { 'X-ClickHouse-User': user, 'X-ClickHouse-Key': password },
    body: body === null ? sql : body,
  });
  const text = (await response.text()).trim();
  if (!response.ok) throw new Error(`${response.status} ${text.slice(0, 300)}`);
  return text;
}

/** A value as a ClickHouse string literal.
 *
 *  The backslash is replaced FIRST and that order is the whole of it: escaping
 *  the quote first would produce a `\\'` whose backslash the next pass would
 *  then double, and a password containing a backslash would authenticate as
 *  something else — or end the literal early and leave the rest of it parsed as
 *  SQL. The generated reader passwords are alphanumeric, so nothing has ever hit
 *  this; the password arrives from the environment, so the script does not get
 *  to assume that.
 *
 *  The three whitespace escapes are here because they cannot be represented
 *  literally inside a statement this script builds by concatenation. */
function literal(value) {
  const escaped = String(value)
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/\n/g, '\\n')
    .replace(/\r/g, '\\r')
    .replace(/\0/g, '\\0');
  return `'${escaped}'`;
}

function statements(sql) {
  return sql
    .split(';')
    .map((chunk) => chunk.trim())
    .filter((chunk) => chunk.length > 0 && chunk.split('\n').some((line) => line.trim() && !line.trim().startsWith('--')));
}

(async () => {
  const schema = fs.readFileSync(path.join(DIR, 'schema.sql'), 'utf8');
  const tables = fs
    .readdirSync(DIR)
    .filter((name) => name.endsWith('.tsv'))
    .map((name) => name.replace(/\.tsv$/, ''))
    .sort();

  console.log(`== schema: ${statements(schema).length} statement(s)`);
  for (const statement of statements(schema)) await run(`${statement};`);
  console.log('   applied');

  console.log('== load');
  for (const table of tables) {
    const file = path.join(DIR, `${table}.tsv`);
    const rows = fs.readFileSync(file, 'utf8').split('\n').filter(Boolean).length;
    await run(`INSERT INTO ${DATABASE}.${PREFIX}${table} FORMAT TabSeparated`, {
      body: fs.readFileSync(file),
    });
    const counted = await run(`SELECT count() FROM ${DATABASE}.${PREFIX}${table}`);
    const ok = Number(counted) === rows;
    console.log(`   ${(PREFIX + table).padEnd(38)} ${String(rows).padStart(7)} rows  loaded=${counted} ${ok ? 'ok' : 'MISMATCH'}`);
    if (!ok) throw new Error(`${PREFIX}${table}: ${rows} generated, ${counted} in the table`);
  }

  console.log('== reader');
  // Re-runnable: the card's secrets are rewritten by the same seed that rewrites
  // this account, so recreating it with the current password is the correct
  // behaviour rather than a special case.
  await run(`DROP USER IF EXISTS ${READER}`);
  await run(
    `CREATE USER ${READER} IDENTIFIED WITH sha256_password BY ${literal(READER_PASSWORD)} `
      + "HOST IP '10.10.1.0/24' HOST IP '127.0.0.1' SETTINGS readonly = 2",
  );
  for (const table of tables) {
    await run(`GRANT SELECT ON ${DATABASE}.${PREFIX}${table} TO ${READER}`);
  }
  console.log(`   ${READER}: created, SELECT on ${tables.length} table(s), readonly = 2`);
})().catch((error) => {
  console.log('FAILED', error.message);
  process.exit(1);
});
