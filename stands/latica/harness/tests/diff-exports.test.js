// `diff-exports.js`, the rehearsal's verdict — checked against the failures it
// exists to catch.
//
//   node --test stands/latica/harness/tests/
//
// The first rehearsal (SUP-917) read two manifests and compared counts. That
// found 15 → 37, and it would have missed a changed DEFINITION under an
// unchanged name — counts equal, diff clean, demonstration wrong. So each case
// here is one shape of difference, and the last two are the ones a count
// comparison cannot see.
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFile } = require('node:child_process');

const SCRIPT = path.join(__dirname, '..', 'diff-exports.js');
const BACKUP = path.join(__dirname, '..', '..', 'configuration-backup');

/** A copy of the committed export, optionally bent out of shape. */
function copy(mutate = () => {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'diff-exports-'));
  for (const name of fs.readdirSync(BACKUP)) {
    if (name.endsWith('.json')) fs.copyFileSync(path.join(BACKUP, name), path.join(dir, name));
  }
  mutate(dir, {
    load: (name) => JSON.parse(fs.readFileSync(path.join(dir, name), 'utf8')),
    save: (name, body) => fs.writeFileSync(path.join(dir, name), JSON.stringify(body, null, 1)),
  });
  return dir;
}

function diff(before, after) {
  return new Promise((resolve) => {
    execFile(process.execPath, [SCRIPT], {
      encoding: 'utf8',
      timeout: 30000,
      env: { PATH: process.env.PATH, HOME: process.env.HOME, BEFORE: before, AFTER: after },
    }, (error, stdout, stderr) => {
      resolve({ status: error ? (error.code ?? 1) : 0, out: `${stdout ?? ''}${stderr ?? ''}` });
    });
  });
}

test('the same export against itself is clean', async () => {
  const result = await diff(BACKUP, BACKUP);
  assert.equal(result.status, 0, result.out);
  assert.match(result.out, /no differences/);
  assert.match(result.out, /library items: BEFORE 15, AFTER 15 — 15 identical/);
  assert.match(result.out, /dashboards: BEFORE 2, AFTER 2 — 2 identical/);
  assert.match(result.out, /autopilots: BEFORE 2, AFTER 2 — 2 identical/);
  assert.match(result.out, /tool servers: BEFORE 1, AFTER 1 — 1 identical/);
  assert.match(result.out, /Cypress Bay Digestive Health: identical \(4 datasets/);
});

test('a rebuilt export with new ids throughout is still clean', async () => {
  // What a correct restore produces: the same definitions under different ids.
  // Nothing in the verdict may depend on an id.
  let minted = 0;
  const fresh = () => `rebuilt-${(minted += 1)}`;
  const after = copy((dir, { load, save }) => {
    const itemIdByOld = new Map(load('library-items.json').map((one) => [one.id, fresh()]));
    save('library-items.json', load('library-items.json').map((one) => ({ ...one, id: itemIdByOld.get(one.id) })));
    save('dashboards.json', load('dashboards.json').map((board) => ({
      ...board,
      id: fresh(),
      layout: {
        ...board.layout,
        items: board.layout.items.map((cell) => ({
          ...cell, id: fresh(), libraryItemId: itemIdByOld.get(cell.libraryItemId),
        })),
      },
      items: board.items.map((one) => ({ ...one, id: fresh() })),
      widgetFilterBindings: board.widgetFilterBindings.map((one) => ({ ...one, layoutItemId: fresh() })),
    })));
    save('autopilots.json', load('autopilots.json').map((one) => ({ ...one, id: fresh() })));
    save('mcp-servers.json', load('mcp-servers.json').map((one) => ({ ...one, id: fresh() })));
  });
  const result = await diff(BACKUP, after);
  assert.equal(result.status, 0, result.out);
  assert.match(result.out, /no differences/);
});

test('the 15 → 37 shape is caught and named', async () => {
  const after = copy((dir, { load, save }) => {
    const items = load('library-items.json');
    save('library-items.json', [...items, ...items.map((one) => ({ ...one, id: `${one.id}-again` }))]);
    const boards = load('dashboards.json');
    save('dashboards.json', [...boards, ...boards.map((one) => ({ ...one, id: `${one.id}-again` }))]);
  });
  const result = await diff(BACKUP, after);
  assert.equal(result.status, 1);
  assert.match(result.out, /library items: BEFORE 15, AFTER 30 — 15 identical, 0 changed, 0 missing, 15 extra/);
  assert.match(result.out, /dashboards: BEFORE 2, AFTER 4/);
  assert.match(result.out, /is in AFTER and not in BEFORE/);
  assert.match(result.out, /difference\(s\)\. This is not a clean restore/);
});

test('the 2 → 0 autopilot shape is caught and named', async () => {
  const after = copy((dir, { load, save }) => save('autopilots.json', []));
  const result = await diff(BACKUP, after);
  assert.equal(result.status, 1);
  assert.match(result.out, /autopilots: BEFORE 2, AFTER 0/);
  assert.match(result.out, /autopilots: "Book the recall" is missing from AFTER/);
  assert.match(result.out, /autopilots: "Surveillance overdue" is missing from AFTER/);
});

test('a lost tool server is caught, which is what loses the autopilots', async () => {
  const after = copy((dir, { load, save }) => save('mcp-servers.json', []));
  const result = await diff(BACKUP, after);
  assert.equal(result.status, 1);
  assert.match(result.out, /tool servers: "scheduling" is missing from AFTER/);
});

test('a tool server that came back switched off, or without its credential, is caught', async () => {
  const after = copy((dir, { load, save }) => save(
    'mcp-servers.json',
    load('mcp-servers.json').map((one) => ({ ...one, enabled: false, hasAuth: false, authHeaderNames: [] })),
  ));
  const result = await diff(BACKUP, after);
  assert.equal(result.status, 1);
  assert.match(result.out, /tool servers: "scheduling" differs in .*enabled/);
  assert.match(result.out, /authHeaderNames/);
});

test('a semantic model short of a dataset is caught, by name', async () => {
  const after = copy((dir, { load, save }) => {
    const model = load('semantic-model.cypress-bay-digestive-health.json');
    save('semantic-model.cypress-bay-digestive-health.json', {
      ...model, datasets: model.datasets.filter((one) => one.code !== 'procedures'),
    });
  });
  const result = await diff(BACKUP, after);
  assert.equal(result.status, 1);
  assert.match(result.out, /the model of "Cypress Bay Digestive Health" differs/);
  assert.match(result.out, /datasets "procedures" missing/);
});

// --- the two a count comparison cannot see -------------------------------

test('a changed DEFINITION under an unchanged name is caught, though the counts match', async () => {
  const after = copy((dir, { load, save }) => {
    const items = load('library-items.json');
    items[2] = { ...items[2], customSql: 'SELECT 1', visualizationSpec: { mark: 'line' } };
    save('library-items.json', items);
  });
  const result = await diff(BACKUP, after);
  assert.equal(result.status, 1);
  assert.match(result.out, /library items: BEFORE 15, AFTER 15 — 14 identical, 1 changed/);
  assert.match(result.out, /differs in .*(customSql|visualizationSpec)/);
});

test('a widget that came back at the wrong place is caught, though every count matches', async () => {
  const after = copy((dir, { load, save }) => {
    const boards = load('dashboards.json');
    const cells = boards[0].layout.items;
    boards[0].layout.items = [{ ...cells[0], position: { x: 9, y: 9, w: 1, h: 1 } }, ...cells.slice(1)];
    save('dashboards.json', boards);
  });
  const result = await diff(BACKUP, after);
  assert.equal(result.status, 1);
  assert.match(result.out, /dashboards: BEFORE 2, AFTER 2 — 2 identical/);
  assert.match(result.out, /widget missing —/);
  assert.match(result.out, /widget extra —/);
});

test('a board that came back one widget short is caught', async () => {
  const after = copy((dir, { load, save }) => {
    const boards = load('dashboards.json');
    boards[0].layout.items = boards[0].layout.items.slice(1);
    save('dashboards.json', boards);
  });
  const result = await diff(BACKUP, after);
  assert.equal(result.status, 1);
  assert.match(result.out, /7 widget\(s\) in BEFORE, 6 in AFTER/);
});

test('a tenant the rebuilt stand has and the export never had a model for is not a difference', async () => {
  // A rebuilt stand carries the three organisations the listing declares plus
  // whatever step 0 made. An empty model on one of them is not something the
  // restore lost.
  const after = copy((dir, { load, save }) => {
    const manifest = load('MANIFEST.json');
    save('semantic-model.a-fourth-tenant.json', { version: 1, datasets: [], dimensions: [], hierarchies: [], bindings: [], metrics: [] });
    manifest.tenants.push({
      id: 'new', name: 'A Fourth Tenant', file: 'semantic-model.a-fourth-tenant.json',
      counts: { datasets: 0, dimensions: 0, metrics: 0, hierarchies: 0 },
    });
    save('MANIFEST.json', manifest);
  });
  const result = await diff(BACKUP, after);
  assert.equal(result.status, 0, result.out);
  assert.match(result.out, /A Fourth Tenant: in AFTER only, and empty — not a difference/);
});
