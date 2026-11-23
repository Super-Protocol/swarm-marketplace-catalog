// Prove each clinic reader can read its own eleven tables and nothing else on
// that server, and that it can still run the kind of query the analyst sends.
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

// READERS is a JSON array of { own, user, password } — the table prefix a reader
// is entitled to and the account. What it must NOT be able to read is derived
// rather than listed: every other clinic prefix on the server, read with the
// owner credential, so a reader added to this database later is covered without
// anybody remembering to extend a list here.
const READERS = JSON.parse(process.env.READERS);

/** One REAL table per prefix on the server, read with the owner credential.
 *
 *  A real one and not `<prefix>patients`: the other datasets on this server are
 *  not clinic corpora and do not all have a `patients`, and a probe at a table
 *  that does not exist answers `UNKNOWN_TABLE` rather than `ACCESS_DENIED` —
 *  which proves nothing about the grant and reads as a failure. */
async function tableByPrefix() {
  const text = await fetch(URL, {
    method: 'POST',
    headers: { 'X-ClickHouse-User': ROW.username, 'X-ClickHouse-Key': OWNER_PASSWORD },
    body: `SELECT concat(splitByChar('_', name)[1], '_') AS pfx, min(name) FROM system.tables `
      + `WHERE database='${DB}' GROUP BY pfx ORDER BY pfx FORMAT TSV`,
  }).then((response) => response.text());
  return new Map(
    text
      .trim()
      .split('\n')
      .filter(Boolean)
      .map((line) => line.split('\t')),
  );
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

    const [ownStatus, rows] = await ask(reader, `SELECT count() FROM ${DB}.${reader.own}patients`);
    check(ownStatus === 200 && Number(rows) > 0, `reads its own ${reader.own}patients (${rows})`);

    for (const [, table] of others) {
      const [status, text] = await ask(reader, `SELECT count() FROM ${DB}.${table}`);
      check(status !== 200 && /ACCESS_DENIED|Not enough privileges/.test(text), `refused ${table}`);
    }

    const [, listed] = await ask(
      reader,
      `SELECT count() FROM system.tables WHERE database='${DB}' AND name LIKE '${reader.own}%' FORMAT TSV`,
    );
    check(Number(listed) === 11, `sees exactly its own 11 tables in system.tables (${listed})`);
    const [, foreign] = await ask(
      reader,
      `SELECT count() FROM system.tables WHERE database='${DB}' AND name NOT LIKE '${reader.own}%' FORMAT TSV`,
    );
    check(Number(foreign) === 0, `sees no other dataset's tables at all (${foreign})`);

    const [insertStatus, insertText] = await ask(
      reader,
      `INSERT INTO ${DB}.${reader.own}sites VALUES ('X','x','x','XX',1,'2026-01-01')`,
    );
    check(insertStatus !== 200 && /ACCESS_DENIED|READONLY|Not enough privileges/.test(insertText), 'refused an INSERT');

    const [dropStatus, dropText] = await ask(reader, `DROP TABLE ${DB}.${reader.own}sites`);
    check(dropStatus !== 200 && /ACCESS_DENIED|READONLY|Not enough privileges/.test(dropText), 'refused a DROP TABLE');

    // What the analyst actually sends. Under `readonly = 1` this is the one that
    // comes back `Code: 164` and takes the whole stand's dialog with it.
    const [settingsStatus, settingsText] = await ask(
      reader,
      `SELECT count() FROM ${DB}.${reader.own}procedures SETTINGS max_execution_time = 30, max_result_rows = 100, result_overflow_mode = 'break'`,
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
