// `restore-stand.js`, run for real against a stand that only exists in memory —
// twice, which is the whole point.
//
//   node --test stands/latica/harness/tests/
//
// The acceptance criterion on SUP-939 is a rehearsal whose diff comes out clean:
// export, rebuild, restore, export, and nothing differs. A rehearsal costs a
// cluster space and an afternoon. This is the same shape of check against
// `tests/fake-stand.js`: the real script, its real mutations, in their real
// order, over the real export in `configuration-backup/` — and then again,
// counting the writes. A second run that writes anything at all is the defect
// that turned 15 library items into 37 on 2026-11-25, and it fails here.
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFile } = require('node:child_process');

const { fakeStand } = require('./fake-stand');
const C = require('../stand-config');
const R = require('../reconcile');

const SCRIPT = path.join(__dirname, '..', 'restore-stand.js');
const BACKUP = path.join(__dirname, '..', '..', 'configuration-backup');

const SCHEDULE_RECALL = [{
  name: 'schedule_recall', boundName: 'mcp_scheduling_schedule_recall',
  title: null, description: null, inputSchema: {}, readOnly: false,
}];

/** The export, plus the tool server the autopilots depend on — which the
 *  committed backup predates — and a token file for it. */
function exportDirectory() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'restore-e2e-'));
  for (const name of fs.readdirSync(BACKUP)) {
    if (name.endsWith('.json')) fs.copyFileSync(path.join(BACKUP, name), path.join(dir, name));
  }
  fs.writeFileSync(path.join(dir, 'mcp-servers.json'), JSON.stringify([{
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
    lastError: null,
    lastCheckedAt: null,
    tools: SCHEDULE_RECALL.map(({ name, boundName, readOnly }) => ({ name, boundName, readOnly })),
  }], null, 1));
  const manifest = JSON.parse(fs.readFileSync(path.join(dir, 'MANIFEST.json'), 'utf8'));
  manifest.counts.mcpServers = 1;
  fs.writeFileSync(path.join(dir, 'MANIFEST.json'), JSON.stringify(manifest, null, 1));
  fs.writeFileSync(path.join(dir, 'clinic.token'), 'a-token-the-clinic-issued\n');
  fs.writeFileSync(path.join(dir, 'password'), 'whatever\n');
  return dir;
}

/**
 * Run the script against the fake stand. ASYNCHRONOUSLY, and that is not a
 * style choice: the stand is an HTTP server in this very process, and
 * `spawnSync` blocks the event loop that would have to answer it — the restore
 * would wait for a sign-in page that cannot be served until the restore exits.
 */
function restore(stand, dir, extra = {}) {
  return new Promise((resolve) => {
    execFile(process.execPath, [SCRIPT], {
      encoding: 'utf8',
      timeout: 60000,
      env: {
        PATH: process.env.PATH,
        HOME: process.env.HOME,
        APP_BASE: stand.url,
        CONFIRM_TARGET: stand.url,
        APP_USER: 'gate-rehearsal@local.test',
        PASS_FILE: path.join(dir, 'password'),
        MCP_TOKEN_FILE: path.join(dir, 'clinic.token'),
        IN: dir,
        // No Keycloak and no cards: the researcher tenant is not part of this
        // export, and steps 0 and 0b are skipped with a line that says so.
        CARDS: path.join(dir, 'no-cards-here'),
        ...extra,
      },
    }, (error, stdout, stderr) => {
      resolve({
        status: error ? (error.code ?? 1) : 0,
        out: `${stdout ?? ''}${stderr ?? ''}`,
      });
    });
  });
}

/** What the stand holds, in the shape the export would have written. */
const snapshot = (stand) => ({
  items: stand.store.libraryItems.length,
  folders: stand.store.libraryFolders.length,
  dashboards: stand.store.dashboards.length,
  widgets: stand.store.dashboards.reduce((all, one) => all + (one.layout.items ?? []).length, 0),
  autopilots: stand.store.autopilots.length,
  mcpServers: stand.store.mcpServers.length,
  titles: stand.store.libraryItems.map((one) => one.title).sort(),
  boards: stand.store.dashboards.map((one) => one.title).sort(),
  pilots: stand.store.autopilots.map((one) => one.name).sort(),
});

const withStand = async (seed, body) => {
  const stand = await fakeStand(seed);
  try {
    await body(stand);
  } finally {
    await stand.close();
  }
};

const SEED = { mcpCatalogue: { scheduling: SCHEDULE_RECALL } };

test('a rebuilt stand comes back whole, and a second run writes nothing', async () => {
  const dir = exportDirectory();
  await withStand(SEED, async (stand) => {
    const first = await restore(stand, dir);
    assert.equal(first.status, 0, first.out);

    const after = snapshot(stand);
    assert.deepEqual(
      { items: after.items, dashboards: after.dashboards, autopilots: after.autopilots, mcpServers: after.mcpServers, widgets: after.widgets },
      { items: 15, dashboards: 2, autopilots: 2, mcpServers: 1, widgets: 12 },
      first.out,
    );
    assert.deepEqual(after.boards, [
      'Endoscopy Clinical Quality & Safety',
      'Endoscopy Operational Volume & Provider Productivity',
    ]);
    assert.deepEqual(after.pilots, ['Book the recall', 'Surveillance overdue']);
    // The clinic's model landed in the clinic's tenant, and nothing was
    // imported into the two that have none.
    assert.equal(stand.store.models.size, 1);
    const clinic = stand.store.tenants.find((one) => one.name === 'Cypress Bay Digestive Health');
    assert.equal(stand.store.models.get(clinic.id).datasets.length, 4);

    // And now the part SUP-917 could not get past.
    stand.reset();
    const second = await restore(stand, dir);
    assert.equal(second.status, 0, second.out);
    assert.deepEqual(
      stand.mutations().map((one) => one.token),
      [],
      `the second run wrote: ${stand.mutations().map((one) => one.token).join(', ')}\n${second.out}`,
    );
    assert.deepEqual(snapshot(stand), after, 'the second run changed the stand');
    assert.match(second.out, /0 created, 0 updated/);
  });
});

test('three runs leave 15 library items and 2 dashboards, not 37 and 4', async () => {
  // The measurement from the rehearsal, repeated. It is the headline number on
  // SUP-917 and the reason this file exists.
  const dir = exportDirectory();
  await withStand(SEED, async (stand) => {
    for (let run = 1; run <= 3; run += 1) {
      const result = await restore(stand, dir);
      assert.equal(result.status, 0, `run ${run}: ${result.out}`);
    }
    const after = snapshot(stand);
    assert.equal(after.items, 15);
    assert.equal(after.dashboards, 2);
    assert.equal(after.autopilots, 2);
    assert.equal(after.widgets, 12);
    assert.equal(new Set(after.titles).size, 13, 'two of the fifteen titles are shared by a pair');
  });
});

test('the tool server is registered, switched on, and only then are the autopilots created', async () => {
  const dir = exportDirectory();
  await withStand(SEED, async (stand) => {
    assert.equal((await restore(stand, dir)).status, 0);
    const order = stand.calls.filter((one) => one.mutation).map((one) => one.token);
    const enabled = order.indexOf('setMcpServerEnabled(');
    const firstPilot = order.indexOf('createAutopilot(');
    assert.ok(enabled >= 0, 'the server was switched on');
    assert.ok(firstPilot > enabled, 'an autopilot was created before the tool server was switched on');
    const [server] = stand.store.mcpServers;
    assert.equal(server.enabled, true);
    assert.equal(server.toolCount, 1);
    assert.deepEqual(server.authHeaderNames, ['Authorization']);
    const booker = stand.store.autopilots.find((one) => one.name === 'Book the recall');
    assert.deepEqual(booker.allowedMcpTools, ['mcp_scheduling_schedule_recall']);
    assert.equal(booker.eventSubscriptions.length, 1);
    assert.deepEqual(booker.eventSubscriptions[0].signalFilter.codes, ['SURV-01']);
  });
});

test('a clinic that has not approved this deployment stops the restore before it writes', async () => {
  // The rebuilt deployment attests a new digest, so the clinic answers 403 and
  // the tool server resolves nothing. Everything about that is knowable before
  // the first write, and the refusal says what to do about it.
  const dir = exportDirectory();
  await withStand({ mcpCatalogue: {} }, async (stand) => {
    const result = await restore(stand, dir);
    assert.equal(result.status, 1);
    assert.match(result.out, /PRECONDITIONS NOT MET — nothing has been changed/);
    assert.match(result.out, /did not answer a tool list/);
    assert.match(result.out, /approvedDigest/);
    assert.deepEqual(stand.mutations(), [], 'a failed precondition wrote something');
    assert.deepEqual(
      { items: stand.store.libraryItems.length, dashboards: stand.store.dashboards.length },
      { items: 0, dashboards: 0 },
    );
  });
});

test('an export whose token file is missing is refused, and nothing is written', async () => {
  const dir = exportDirectory();
  fs.rmSync(path.join(dir, 'clinic.token'));
  await withStand(SEED, async (stand) => {
    const result = await restore(stand, dir);
    assert.equal(result.status, 1);
    assert.match(result.out, /no token file at/);
    assert.deepEqual(stand.mutations(), []);
  });
});

test('an installation that does not publish a subscribed event is refused before it writes', async () => {
  const dir = exportDirectory();
  await withStand({ ...SEED, events: ['something.else'] }, async (stand) => {
    const result = await restore(stand, dir);
    assert.equal(result.status, 1);
    assert.match(result.out, /names the event "signal\.created", which this installation does not publish/);
    assert.deepEqual(stand.mutations(), []);
  });
});

test('a stand missing a tenant the export has a model for is refused before it writes', async () => {
  const dir = exportDirectory();
  await withStand({ ...SEED, tenants: ['Latica', 'Pharma Partner'] }, async (stand) => {
    const result = await restore(stand, dir);
    assert.equal(result.status, 1);
    assert.match(result.out, /no tenant named "Cypress Bay Digestive Health"/);
    assert.deepEqual(stand.mutations(), []);
  });
});

test('DRY_RUN reports the plan and writes nothing', async () => {
  const dir = exportDirectory();
  await withStand(SEED, async (stand) => {
    const result = await restore(stand, dir, { DRY_RUN: 'yes' });
    assert.equal(result.status, 0, result.out);
    assert.match(result.out, /library items {2}15 to create/);
    assert.match(result.out, /DRY RUN — nothing was written/);
    assert.deepEqual(stand.mutations(), []);
  });
});

test('a drifted item is corrected in place and nothing is duplicated', async () => {
  const dir = exportDirectory();
  await withStand(SEED, async (stand) => {
    assert.equal((await restore(stand, dir)).status, 0);
    const target = stand.store.libraryItems.find((one) => one.title === 'Total Procedures');
    const id = target.id;
    target.customSql = 'SELECT 1 -- somebody edited this';
    target.description = 'edited';

    stand.reset();
    const again = await restore(stand, dir);
    assert.equal(again.status, 0, again.out);
    assert.equal(stand.store.libraryItems.length, 15, 'it made a second copy instead of correcting the first');
    const corrected = stand.store.libraryItems.find((one) => one.id === id);
    assert.ok(corrected, 'the item was replaced rather than updated');
    assert.notEqual(corrected.customSql, 'SELECT 1 -- somebody edited this');
    assert.deepEqual(stand.mutations().map((one) => one.token), ['updateLibraryItem(']);
  });
});

test('a widget somebody moved is nudged back, not added again', async () => {
  const dir = exportDirectory();
  await withStand(SEED, async (stand) => {
    assert.equal((await restore(stand, dir)).status, 0);
    const board = stand.store.dashboards[0];
    const cell = board.layout.items[0];
    const was = { ...cell.position };
    cell.position = { x: 11, y: 11, w: 1, h: 1 };

    stand.reset();
    assert.equal((await restore(stand, dir)).status, 0);
    assert.equal(board.layout.items.length, 7, 'a widget was added rather than moved');
    assert.deepEqual(board.layout.items[0].position, was);
    assert.ok(stand.mutations().some((one) => one.token === 'updateDashboardLayout('));
    assert.ok(!stand.mutations().some((one) => one.token === 'addItemToDashboard('));
  });
});

test('an autopilot somebody deleted comes back, and the other is left alone', async () => {
  const dir = exportDirectory();
  await withStand(SEED, async (stand) => {
    assert.equal((await restore(stand, dir)).status, 0);
    const survivor = stand.store.autopilots.find((one) => one.name === 'Surveillance overdue');
    stand.store.autopilots = [survivor];

    stand.reset();
    assert.equal((await restore(stand, dir)).status, 0);
    assert.deepEqual(stand.store.autopilots.map((one) => one.name).sort(), ['Book the recall', 'Surveillance overdue']);
    assert.equal(stand.store.autopilots.find((one) => one.name === 'Surveillance overdue').id, survivor.id);
    assert.deepEqual(stand.mutations().map((one) => one.token), ['createAutopilot(']);
  });
});

test('something somebody added afterwards is reported and left alone, unless PRUNE says otherwise', async () => {
  const dir = exportDirectory();
  await withStand(SEED, async (stand) => {
    assert.equal((await restore(stand, dir)).status, 0);
    stand.store.libraryItems.push({
      id: 'theirs', title: 'A chart Denis made this morning', type: 'CHART', visibility: 'TENANT',
      visualizationSpec: {}, kind: 'semantic', query: null, tags: null, folderId: null, version: 1,
    });

    stand.reset();
    const kept = await restore(stand, dir);
    assert.equal(kept.status, 0, kept.out);
    assert.match(kept.out, /A chart Denis made this morning.*left alone/);
    assert.equal(stand.store.libraryItems.length, 16);
    assert.deepEqual(stand.mutations(), []);

    stand.reset();
    const pruned = await restore(stand, dir, { PRUNE: 'yes' });
    assert.equal(pruned.status, 0, pruned.out);
    assert.equal(stand.store.libraryItems.length, 15);
    assert.deepEqual(stand.mutations().map((one) => one.token), ['deleteLibraryItem(']);
  });
});

test('what came back is what was exported, field for field', async () => {
  // The rehearsal's own acceptance test, run here: export, restore, export, and
  // compare the definitions rather than the counts.
  const dir = exportDirectory();
  await withStand(SEED, async (stand) => {
    assert.equal((await restore(stand, dir)).status, 0);
    const wanted = JSON.parse(fs.readFileSync(path.join(dir, 'library-items.json'), 'utf8'));
    const plan = R.match(
      wanted.map((one) => C.libraryShape(one, new Map())),
      stand.store.libraryItems.map((one) => C.libraryShape(one, new Map())),
      { keyFor: R.libraryKey, same: R.librarySame },
    );
    assert.ok(R.isNoOp(plan), JSON.stringify(R.counts(plan)));
    assert.equal(plan.extra.length, 0);

    const boards = JSON.parse(fs.readFileSync(path.join(dir, 'dashboards.json'), 'utf8'));
    const boardPlan = R.match(
      boards.map(C.dashboardShape),
      stand.store.dashboards.map(C.dashboardShape),
      { keyFor: R.dashboardKey, same: R.dashboardSame },
    );
    assert.ok(R.isNoOp(boardPlan), JSON.stringify(R.counts(boardPlan)));

    const pilots = JSON.parse(fs.readFileSync(path.join(dir, 'autopilots.json'), 'utf8'));
    const pilotPlan = R.match(
      pilots.map(C.autopilotShape),
      stand.store.autopilots.map((one) => C.autopilotShape({
        ...one,
        eventSubscriptions: (one.eventSubscriptions ?? []).map((sub) => ({
          ...sub,
          eventName: stand.store.events.find((event) => event.id === sub.eventId)?.name,
        })),
      })),
      { keyFor: R.autopilotKey, same: R.autopilotSame },
    );
    assert.ok(R.isNoOp(pilotPlan), JSON.stringify(R.counts(pilotPlan)));
  });
});
