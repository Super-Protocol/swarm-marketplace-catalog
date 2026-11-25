// The property the rehearsal disproved, pinned: a restore run against a stand
// that already holds the export has nothing to do.
//
//   node --test stands/latica/harness/tests/
//
// On 2026-11-25 `restore-stand.js` was run three times against a rebuilt stand
// and left 37 library items where the export has 15, and 4 dashboards where it
// has 2 (SUP-917). The cause was not a mistake in a mutation, it was the absence
// of any matching at all. So these tests work on the real export in
// `configuration-backup/` — including its two pairs of same-titled library
// items, which is what makes the case interesting — and assert the plan, not the
// writes: `reconcile.js` is pure, and a plan with nothing in `create` and
// nothing in `update` is a run that writes nothing.
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const R = require('../reconcile');
const C = require('../stand-config');

const BACKUP = path.join(__dirname, '..', '..', 'configuration-backup');
const load = (name) => JSON.parse(fs.readFileSync(path.join(BACKUP, name), 'utf8'));

const EXPORT = {
  items: load('library-items.json'),
  folders: load('library-folders.json'),
  boards: load('dashboards.json'),
  pilots: load('autopilots.json'),
  manifest: load('MANIFEST.json'),
};

/** The stand's own tool server, as `export-stand.js` now captures it. The
 *  export in the repository predates that field, so the fixture is written out
 *  here rather than read — the shape is what matters. */
const MCP_EXPORT = [{
  id: 'old-mcp-1',
  name: 'Cypress Bay scheduling',
  slug: 'scheduling',
  url: 'https://cypress-bay-scheduling.demo.superprotocol.dev/mcp',
  description: 'Outbound traffic carries only the action the clinic authorised…',
  enabled: true,
  global: false,
  enabledForTenant: true,
  hasAuth: true,
  authHeaderNames: ['Authorization'],
  toolCount: 1,
  tools: [{ name: 'schedule_recall', boundName: 'mcp_scheduling_schedule_recall', readOnly: false }],
}];

let minted = 0;
const freshId = () => `new-${(minted += 1)}`;

/**
 * What a CORRECT restore leaves behind: the same definitions, under the ids a
 * rebuilt stand mints. Nothing here reads `reconcile.js`, so a test that passes
 * is a statement about the matching rather than about this function.
 */
function rebuilt(source = EXPORT, mcp = MCP_EXPORT) {
  const folderIdByOld = new Map(source.folders.map((one) => [one.id, freshId()]));
  const libraryFolders = source.folders.map((one) => ({
    ...one,
    id: folderIdByOld.get(one.id),
    parentId: one.parentId ? folderIdByOld.get(one.parentId) : null,
  }));
  const itemIdByOld = new Map(source.items.map((one) => [one.id, freshId()]));
  const libraryItems = source.items.map((one) => ({
    ...one,
    id: itemIdByOld.get(one.id),
    folderId: one.folderId ? folderIdByOld.get(one.folderId) : null,
    version: 1,
  }));
  const dashboards = source.boards.map((board) => {
    const layoutIdByOld = new Map(C.layoutItemsOf(board.layout).map((cell) => [cell.id, freshId()]));
    const filterIdByOld = new Map((board.filters ?? []).map((one) => [one.id, freshId()]));
    return {
      ...board,
      id: freshId(),
      layout: {
        ...(Array.isArray(board.layout) ? {} : board.layout),
        items: C.layoutItemsOf(board.layout).map((cell) => ({
          ...cell,
          id: layoutIdByOld.get(cell.id),
          libraryItemId: itemIdByOld.get(cell.libraryItemId ?? cell.itemId),
        })),
      },
      filters: (board.filters ?? []).map((one) => ({ ...one, id: filterIdByOld.get(one.id) })),
      widgetFilterBindings: (board.widgetFilterBindings ?? []).map((widget) => ({
        ...widget,
        layoutItemId: layoutIdByOld.get(widget.layoutItemId),
        bindings: (widget.bindings ?? []).map((one) => ({ ...one, filterId: filterIdByOld.get(one.filterId) })),
      })),
    };
  });
  return {
    libraryFolders,
    libraryItems,
    dashboards,
    autopilots: source.pilots.map((one) => ({ ...one, id: freshId() })),
    mcpServers: mcp.map((one) => ({ ...one, id: freshId() })),
  };
}

/** The five plans a restore computes, for one export against one stand. */
function planAll(source, stand) {
  const wantedPaths = C.folderPaths(source.folders);
  const livePaths = C.folderPaths(stand.libraryFolders);
  return {
    folders: R.match(
      source.folders.map((one) => C.folderShape(one, wantedPaths)),
      stand.libraryFolders.map((one) => C.folderShape(one, livePaths)),
      { keyFor: R.folderKey, same: R.folderSame },
    ),
    items: R.match(
      source.items.map((one) => C.libraryShape(one, wantedPaths)),
      stand.libraryItems.map((one) => C.libraryShape(one, livePaths)),
      { keyFor: R.libraryKey, same: R.librarySame },
    ),
    boards: R.match(
      source.boards.map(C.dashboardShape),
      stand.dashboards.map(C.dashboardShape),
      { keyFor: R.dashboardKey, same: R.dashboardSame },
    ),
    pilots: R.match(
      source.pilots.map(C.autopilotShape),
      stand.autopilots.map(C.autopilotShape),
      { keyFor: R.autopilotKey, same: R.autopilotSame },
    ),
    mcp: R.match(
      MCP_EXPORT.map(C.mcpShape),
      stand.mcpServers.filter((one) => !one.global).map(C.mcpShape),
      { keyFor: R.mcpKey, same: R.mcpSame },
    ),
  };
}

const EMPTY = {
  libraryFolders: [], libraryItems: [], dashboards: [], autopilots: [], mcpServers: [],
};

// ---------------------------------------------------------------------------

test('the export is the one the stand was measured at: 15 items, 2 boards, 2 autopilots', () => {
  assert.equal(EXPORT.items.length, 15);
  assert.equal(EXPORT.boards.length, 2);
  assert.equal(EXPORT.pilots.length, 2);
  assert.deepEqual(EXPORT.manifest.counts.libraryItems, 15);
});

test('two pairs of library items share a title, so a title alone is a bucket and not a key', () => {
  const byTitle = new Map();
  for (const item of EXPORT.items) byTitle.set(item.title, (byTitle.get(item.title) ?? 0) + 1);
  const shared = [...byTitle.entries()].filter(([, count]) => count > 1);
  assert.equal(shared.length, 2, 'the fixture must keep the duplicate titles — they are the hard case');
  // And their definitions differ — one pair in its query and both in their
  // visualization spec — which is why both of each pair have to come back.
  for (const [title] of shared) {
    const pair = EXPORT.items
      .filter((one) => one.title === title)
      .map((one) => C.libraryShape(one, new Map()));
    assert.ok(!R.librarySame(pair[0], pair[1]), `the two "${title}" items are identical in the fixture`);
  }
});

test('a fresh stand is planned as creates, one per exported thing', () => {
  const plan = planAll(EXPORT, EMPTY);
  assert.equal(plan.items.create.length, 15);
  assert.equal(plan.boards.create.length, 2);
  assert.equal(plan.pilots.create.length, 2);
  assert.equal(plan.mcp.create.length, 1);
  for (const one of Object.values(plan)) {
    assert.equal(one.update.length, 0);
    assert.equal(one.extra.length, 0);
  }
});

test('a restored stand is a no-op: nothing to create and nothing to update', () => {
  const plan = planAll(EXPORT, rebuilt());
  for (const [name, one] of Object.entries(plan)) {
    assert.equal(one.create.length, 0, `${name}: ${one.create.length} would be created a second time`);
    assert.equal(one.update.length, 0, `${name}: ${one.update.length} would be written for no reason`);
    assert.equal(one.extra.length, 0, `${name}: ${one.extra.length} unaccounted for`);
    assert.ok(R.isNoOp(one));
  }
  assert.equal(plan.items.unchanged.length, 15);
  assert.equal(plan.boards.unchanged.length, 2);
});

test('both same-titled items are matched to their own counterpart, not to each other', () => {
  const plan = planAll(EXPORT, rebuilt());
  for (const { wanted, existing } of plan.items.unchanged) {
    assert.equal(R.signature(wanted.query), R.signature(existing.query));
    assert.equal(wanted.title, existing.title);
  }
  const titles = plan.items.unchanged.map(({ wanted }) => wanted.title);
  assert.equal(new Set(titles).size, 13, 'two titles appear twice among the fifteen');
});

test('the 15 → 37 duplication cannot be planned again: a stand with the export twice has extras, not creates', () => {
  // What three runs of the old script produced, in the shape the new one sees:
  // the export's items twice over. The plan accounts for one copy and reports
  // the other, and under no circumstances adds a third.
  const once = rebuilt();
  const twice = rebuilt();
  const doubled = {
    ...once,
    libraryItems: [...once.libraryItems, ...twice.libraryItems],
    dashboards: [...once.dashboards, ...twice.dashboards],
  };
  const plan = planAll(EXPORT, doubled);
  assert.equal(plan.items.create.length, 0);
  assert.equal(plan.items.update.length, 0);
  assert.equal(plan.items.unchanged.length, 15);
  assert.equal(plan.items.extra.length, 15, 'the second copy is reported, not re-created');
  assert.equal(plan.boards.extra.length, 2);
});

test('a drifted definition is an update in place, never a second item', () => {
  const stand = rebuilt();
  stand.libraryItems[3] = { ...stand.libraryItems[3], customSql: 'SELECT 1', description: 'edited' };
  const plan = planAll(EXPORT, stand);
  assert.equal(plan.items.create.length, 0);
  assert.equal(plan.items.extra.length, 0);
  assert.equal(plan.items.update.length, 1);
  assert.equal(plan.items.unchanged.length, 14);
  assert.equal(plan.items.update[0].existing.source.id, stand.libraryItems[3].id);
});

test('a renamed item is a create and an extra, because a name is the only stable key there is', () => {
  const stand = rebuilt();
  stand.libraryItems[0] = { ...stand.libraryItems[0], title: 'Something else entirely' };
  const plan = planAll(EXPORT, stand);
  assert.equal(plan.items.create.length, 1);
  assert.equal(plan.items.extra.length, 1);
  // And the extra is left alone unless PRUNE says otherwise, which is the
  // conservative half of that trade: the restore does not delete what it cannot
  // identify.
  assert.equal(plan.items.extra[0].title, 'Something else entirely');
});

test('a missing autopilot is created and the other is left alone', () => {
  const stand = rebuilt();
  stand.autopilots = stand.autopilots.filter((one) => one.name !== 'Book the recall');
  const plan = planAll(EXPORT, stand);
  assert.deepEqual(plan.pilots.create.map((one) => one.name), ['Book the recall']);
  assert.equal(plan.pilots.unchanged.length, 1);
});

test('an autopilot that lost its tool allowlist is an update, not a duplicate', () => {
  const stand = rebuilt();
  const index = stand.autopilots.findIndex((one) => (one.allowedMcpTools ?? []).length > 0);
  assert.ok(index >= 0, 'one of the two autopilots allows an MCP tool');
  stand.autopilots[index] = { ...stand.autopilots[index], allowedMcpTools: [] };
  const plan = planAll(EXPORT, stand);
  assert.equal(plan.pilots.create.length, 0);
  assert.equal(plan.pilots.update.length, 1);
  assert.deepEqual(plan.pilots.update[0].wanted.allowedMcpTools, ['mcp_scheduling_schedule_recall']);
});

test('a tool server is matched by slug, and its credential is neither exported nor compared', () => {
  const stand = rebuilt();
  // Same server, read back by a stand that holds the token: the export cannot
  // carry the value and does not have to.
  assert.equal(R.mcpKey(C.mcpShape(stand.mcpServers[0])), R.mcpKey(C.mcpShape(MCP_EXPORT[0])));
  assert.ok(R.isNoOp(planAll(EXPORT, stand).mcp));
  assert.ok(!R.MCP_FIELDS.includes('authHeaders'));
  assert.ok(!JSON.stringify(MCP_EXPORT).includes('Bearer'));
});

test('a tool server that came back switched off is switched on again', () => {
  const stand = rebuilt();
  stand.mcpServers[0] = { ...stand.mcpServers[0], enabled: false };
  const plan = planAll(EXPORT, stand);
  assert.equal(plan.mcp.update.length, 1);
  assert.equal(plan.mcp.update[0].wanted.enabled, true);
});

test('a global tool server is not restored: it is rendered from the listing parameters', () => {
  const stand = rebuilt();
  stand.mcpServers.push({
    id: 'global-1', slug: 'declared', name: 'declared', url: 'https://example.test/mcp',
    global: true, enabled: true, hasAuth: false, authHeaderNames: [], tools: [],
  });
  const plan = planAll(EXPORT, stand);
  assert.ok(R.isNoOp(plan.mcp));
  assert.equal(plan.mcp.extra.length, 0, 'a global row is filtered out before matching, not reported as extra');
});

// --- widgets, which is where the identifiers all change at once -------------

const widgetPlanFor = (board, stand, itemIdByOldId) => R.match(
  C.layoutItemsOf(board.layout).map((cell) => C.widgetShape(cell, (id) => itemIdByOldId.get(id) ?? null)),
  C.layoutItemsOf(stand.layout).map((cell) => C.widgetShape(cell, (id) => id ?? null)),
  { keyFor: R.widgetKey, same: R.widgetSame },
);

/** The mapping step 2 of the restore builds: exported item id → target item id.
 *  Taken from `rebuilt()` by position, which is what a correct restore produces. */
const mappingFor = (stand) => new Map(EXPORT.items.map((one, at) => [one.id, stand.libraryItems[at].id]));

test('every widget on a restored board is matched, and none is added again', () => {
  const stand = rebuilt();
  const mapping = mappingFor(stand);
  let widgets = 0;
  for (const [at, board] of EXPORT.boards.entries()) {
    const plan = widgetPlanFor(board, stand.dashboards[at], mapping);
    assert.equal(plan.create.length, 0, `${board.title}: a widget would be added twice`);
    assert.equal(plan.update.length, 0);
    assert.equal(plan.extra.length, 0);
    widgets += plan.unchanged.length;
  }
  assert.equal(widgets, 12, 'the two boards carry twelve widgets between them');
});

test('a widget that moved is an update, so it is nudged back rather than re-added', () => {
  const stand = rebuilt();
  const mapping = mappingFor(stand);
  const board = stand.dashboards[0];
  const cells = C.layoutItemsOf(board.layout);
  board.layout = { ...board.layout, items: [{ ...cells[0], position: { x: 9, y: 9, w: 1, h: 1 } }, ...cells.slice(1)] };
  const plan = widgetPlanFor(EXPORT.boards[0], board, mapping);
  assert.equal(plan.create.length, 0);
  assert.equal(plan.extra.length, 0);
  assert.equal(plan.update.length, 1);
  assert.deepEqual(
    [plan.update[0].wanted.x, plan.update[0].wanted.y],
    [C.geometryOf(C.layoutItemsOf(EXPORT.boards[0].layout)[0]).x, C.geometryOf(C.layoutItemsOf(EXPORT.boards[0].layout)[0]).y],
  );
});

test('a widget is matched by the item it shows, so a same-titled twin cannot capture it', () => {
  // *Endoscopist Scorecard & Outlier Analysis* is on a board and there are two
  // library items by that name. The widget key is the target-side item id, so
  // the one on the board is the one that was on the board.
  const stand = rebuilt();
  const mapping = mappingFor(stand);
  const board = EXPORT.boards.find((one) => one.items.some(
    (item) => item.title === 'Endoscopist Scorecard & Outlier Analysis',
  ));
  assert.ok(board, 'the fixture has that widget');
  const cell = C.layoutItemsOf(board.layout).find((one) => {
    const item = EXPORT.items.find((candidate) => candidate.id === one.libraryItemId);
    return item?.title === 'Endoscopist Scorecard & Outlier Analysis';
  });
  const twins = EXPORT.items.filter((one) => one.title === 'Endoscopist Scorecard & Outlier Analysis');
  assert.equal(twins.length, 2);
  const shown = C.widgetShape(cell, (id) => mapping.get(id) ?? null);
  assert.equal(shown.itemKey, mapping.get(cell.libraryItemId));
  assert.notEqual(mapping.get(twins[0].id), mapping.get(twins[1].id));
});

// --- the pieces underneath --------------------------------------------------

test('a folder is named by its path, so two "Quality" folders are two folders', () => {
  const folders = [
    { id: 'a', name: 'Clinical', parentId: null },
    { id: 'b', name: 'Quality', parentId: 'a' },
    { id: 'c', name: 'Quality', parentId: null },
  ];
  const paths = C.folderPaths(folders);
  assert.equal(paths.get('b'), 'Clinical/Quality');
  assert.equal(paths.get('c'), 'Quality');
  const plan = R.match(
    folders.map((one) => C.folderShape(one, paths)),
    [folders[0], folders[2]].map((one) => C.folderShape(one, paths)),
    { keyFor: R.folderKey, same: R.folderSame },
  );
  assert.deepEqual(plan.create.map((one) => one.folderPath), ['Clinical/Quality']);
});

test('folderPaths survives a parent that is missing and a cycle', () => {
  assert.equal(C.folderPaths([{ id: 'a', name: 'Orphan', parentId: 'gone' }]).get('a'), 'Orphan');
  const cyclic = [{ id: 'a', name: 'A', parentId: 'b' }, { id: 'b', name: 'B', parentId: 'a' }];
  assert.doesNotThrow(() => C.folderPaths(cyclic));
});

test('null and absent are the same configuration, and field order is not a change', () => {
  const same = R.sameOn(['a', 'b']);
  assert.ok(same({ a: null }, { b: undefined }));
  assert.ok(same({ a: { x: 1, y: 2 } }, { a: { y: 2, x: 1 } }));
  assert.ok(!same({ a: 1 }, { a: '1' }));
});

test('tags are compared as a set, not as a list', () => {
  const left = C.libraryShape({ title: 't', type: 'CHART', tags: ['b', 'a'] }, new Map());
  const right = C.libraryShape({ title: 't', type: 'CHART', tags: ['a', 'b'] }, new Map());
  assert.ok(R.librarySame(left, right));
});

test('the plan is deterministic: the same two lists give the same answer', () => {
  const first = planAll(EXPORT, rebuilt());
  const second = planAll(EXPORT, rebuilt());
  assert.equal(
    JSON.stringify(R.counts(first.items)),
    JSON.stringify(R.counts(second.items)),
  );
});

test('nothing in the export, and nothing this module compares, is a credential', () => {
  for (const name of ['library-items.json', 'dashboards.json', 'autopilots.json', 'mcp-servers.json']) {
    const body = fs.readFileSync(path.join(BACKUP, name), 'utf8');
    for (const field of ['lastDatasetToken', 'thumbnailUrl', 'authHeaders', 'Bearer ']) {
      assert.ok(!body.includes(field), `${name} carries ${field}`);
    }
  }
  // The tool server IS in the export, and its credential is not: the header's
  // name without its value, which is all the API offers and all the restore
  // needs — it reads the token from a file.
  const servers = load('mcp-servers.json');
  assert.deepEqual(servers.map((one) => one.slug), ['scheduling']);
  assert.deepEqual(servers[0].authHeaderNames, ['Authorization']);
  assert.equal(servers[0].hasAuth, true);
  for (const field of C.DROP) assert.ok(!R.LIBRARY_FIELDS.includes(field));
  assert.ok(!R.MCP_FIELDS.includes('authHeaders'));
});
