// Prove each reader can read its own dataset's tables and nothing else on that
// server, and that it can still run the kind of query the analyst sends.
//
// The last part is not a formality. A reader on `readonly = 1` passes every
// grant check here and then refuses every real question, because the analyst's
// ClickHouse adapter sends `max_result_rows` whenever a row limit applies and
// `readonly = 1` refuses a settings change as well as a write. That failure is
// invisible from outside: the gate still reports `allowed` and the card still
// looks connected (SUP-905). So it is checked with a query that changes a
// setting, not with a plain SELECT.
const { createDecipheriv } = require('node:crypto');

const KEY = Buffer.from(process.env.VAULT_KEY, 'base64');
const ROW = JSON.parse(process.env.STORAGE_ROW);
const URL = `http://${ROW.host}:${ROW.port}/`;
const DB = process.env.CH_DATABASE || 'marketplace';

function decrypt(encoded) {
  const [version, iv, tag, ciphertext] = encoded.split(':');
  if (version !== 'v1') throw new Error('unexpected cipher version');
  const decipher = createDecipheriv('aes-256-gcm', KEY, Buffer.from(iv, 'base64'));
  decipher.setAuthTag(Buffer.from(tag, 'base64'));
  return Buffer.concat([decipher.update(Buffer.from(ciphertext, 'base64')), decipher.final()]).toString('utf8');
}

const OWNER_PASSWORD = decrypt(ROW.passwordCipher);

// READERS is a JSON array of { own, user, password, tables? } — the table prefix
// a reader is entitled to, the account, and how many tables it should be able to
// see. What it must NOT be able to read is derived rather than listed: every
// other dataset on the server, read with the owner credential, so a reader added
// to this database later is covered without anybody remembering to extend a list
// here.
//
// `tables` is optional and defaults to eleven, the shape of a clinic corpus. An
// aggregate release is four, so it is a parameter: a reader that can see one
// table more than its dataset has is the failure this check is for, and a count
// hard-coded to one dataset's shape cannot catch it on another's.
const READERS = JSON.parse(process.env.READERS);
const OWNS = READERS.map((reader) => reader.own);

/** Which dataset a table belongs to.
 *
 *  The longest `own` prefix any reader claims, and the first underscore-delimited
 *  segment when none of them does. The fallback alone is not enough once a
 *  dataset's prefix EXTENDS another's: `cbdh_release_procedure_monthly` and
 *  `cbdh_procedures` share a first segment and are two datasets with two owners
 *  and two readers, and grouping them together would quietly stop probing each
 *  against the other. */
function groupOf(name) {
  const claimed = OWNS.filter((prefix) => name.startsWith(prefix)).sort((a, b) => b.length - a.length);
  return claimed[0] || `${name.split('_')[0]}_`;
}

/** One REAL table per dataset on the server, read with the owner credential.
 *
 *  A real one and not `<prefix>patients`: the other datasets on this server are
 *  not clinic corpora and do not all have a `patients`, and a probe at a table
 *  that does not exist answers `UNKNOWN_TABLE` rather than `ACCESS_DENIED` —
 *  which proves nothing about the grant and reads as a failure. */
async function tableByPrefix() {
  const text = await fetch(URL, {
    method: 'POST',
    headers: { 'X-ClickHouse-User': ROW.username, 'X-ClickHouse-Key': OWNER_PASSWORD },
    body: `SELECT name FROM system.tables WHERE database='${DB}' ORDER BY name FORMAT TSV`,
  }).then((response) => response.text());
  const groups = new Map();
  for (const name of text.trim().split('\n').filter(Boolean)) {
    const group = groupOf(name);
    if (!groups.has(group)) groups.set(group, name);
  }
  return new Map([...groups.entries()].sort(([a], [b]) => (a < b ? -1 : 1)));
}

let failures = 0;
const check = (ok, what) => {
  console.log(`  ${ok ? 'ok  ' : 'FAIL'}  ${what}`);
  if (!ok) failures += 1;
};

async function ask(reader, sql) {
  const response = await fetch(URL, {
    method: 'POST',
    headers: { 'X-ClickHouse-User': reader.user, 'X-ClickHouse-Key': reader.password },
    body: sql,
  });
  return [response.status, (await response.text()).trim()];
}

(async () => {
  const sample = await tableByPrefix();
  console.log(`prefixes on this server: ${[...sample.keys()].join(' ')}`);

  for (const reader of READERS) {
    const others = [...sample.entries()].filter(([prefix]) => prefix !== reader.own);
    console.log(
      `\n== ${reader.user} — entitled to ${reader.own}, must not reach `
        + others.map(([prefix]) => prefix).join(' '),
    );

    // Which of its own tables to read is asked of the server rather than named
    // here: a clinic corpus and an aggregate release have different tables, and
    // a probe at a name one of them does not have answers UNKNOWN_TABLE, which
    // proves nothing about the grant and reads as a refusal.
    const [, ownNames] = await ask(
      reader,
      `SELECT name FROM system.tables WHERE database='${DB}' ORDER BY name FORMAT TSV`,
    );
    const mine = ownNames.split('\n').map((line) => line.trim()).filter(Boolean);
    const expected = reader.tables ?? 11;
    check(
      mine.length === expected,
      `sees exactly its own ${expected} tables in system.tables (${mine.length})`,
    );
    check(
      mine.length > 0 && mine.every((name) => name.startsWith(reader.own)),
      `every table it can see carries its own ${reader.own} prefix`,
    );
    if (mine.length === 0) {
      check(false, `${reader.user} can see nothing at all — nothing else is testable`);
      continue;
    }

    for (const table of mine) {
      const [status, rows] = await ask(reader, `SELECT count() FROM ${DB}.${table}`);
      check(status === 200 && Number(rows) > 0, `reads its own ${table} (${rows})`);
    }

    for (const [, table] of others) {
      const [status, text] = await ask(reader, `SELECT count() FROM ${DB}.${table}`);
      check(status !== 200 && /ACCESS_DENIED|Not enough privileges/.test(text), `refused ${table}`);
    }

    // A write that is VALID for whatever shape this dataset has: copying zero of
    // a table's own rows into itself parses against any column list, so what the
    // server refuses is the write and not the statement.
    const [insertStatus, insertText] = await ask(
      reader,
      `INSERT INTO ${DB}.${mine[0]} SELECT * FROM ${DB}.${mine[0]} LIMIT 0`,
    );
    check(insertStatus !== 200 && /ACCESS_DENIED|READONLY|Not enough privileges/.test(insertText), 'refused an INSERT');

    const [dropStatus, dropText] = await ask(reader, `DROP TABLE ${DB}.${mine[0]}`);
    check(dropStatus !== 200 && /ACCESS_DENIED|READONLY|Not enough privileges/.test(dropText), 'refused a DROP TABLE');

    // What the analyst actually sends. Under `readonly = 1` this is the one that
    // comes back `Code: 164` and takes the whole stand's dialog with it.
    const [settingsStatus, settingsText] = await ask(
      reader,
      `SELECT count() FROM ${DB}.${mine[0]} SETTINGS max_execution_time = 30, max_result_rows = 100, result_overflow_mode = 'break'`,
    );
    check(
      settingsStatus === 200,
      `runs a query carrying max_result_rows — readonly = 2, not 1 (${settingsStatus} ${settingsText.slice(0, 60)})`,
    );
  }

  console.log(failures === 0 ? '\neverything checked out' : `\n${failures} check(s) failed`);
  process.exit(failures === 0 ? 0 : 1);
})().catch((error) => {
  console.log('FAILED', error.message);
  process.exit(1);
});
