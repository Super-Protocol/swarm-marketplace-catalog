// Re-apply an export from `export-stand.js` to a REBUILT stand.
//
// This script mutates. It exists because the semantic model, the dashboards,
// the library items and the autopilots live in the deployment's own PostgreSQL,
// and a re-created deployment starts with none of them — so without this the
// rebuild means somebody sitting with the agent for an afternoon again.
//
// Order matters and is not negotiable: the semantic model first, because every
// `kind: semantic` library item names dataset, dimension and metric codes that
// must already exist; then the library items, because a dashboard is a layout
// over items; then the dashboards and their filters; then the autopilots.
//
// Two things it does NOT restore, because no API offers them: a library item's
// version history, and a dashboard's saved versions. Each item is recreated at
// version 1 with its current definition.
//
//   APP_BASE=https://<the rebuilt stand> APP_USER=… PASS_FILE=… \
//   IN=../configuration-backup CONFIRM_TARGET=<the same APP_BASE> \
//   node harness/restore-stand.js
//
// `CONFIRM_TARGET` must repeat `APP_BASE` exactly, and the stand this export
// was taken FROM is refused by name: on 2026-11-23 its owner configured it by
// hand and said in so many words that it must not be disturbed. Overriding
// that needs `ALLOW_LIVE_STAND=yes` and a reason you can defend.
const fs = require('fs');
const path = require('path');
const { signIn, graphql } = require('./applogin');

const FROZEN = 'analyst-stand.conf-apps.superprotocol.dev';

const IMPORT_MODEL = `mutation ($doc: JSON!, $mode: SemanticImportMode!, $t: String) {
  adminSemanticModelImport(document: $doc, mode: $mode, tenantId: $t) {
    created updated deleted calendar invalidatedItems
  }
}`;
const SAVE_ITEM = 'mutation ($i: SaveToLibraryInput!) { saveToLibrary(input: $i) { id title } }';
const SET_TAGS = 'mutation ($id: ID!, $tags: [String!]!) { setLibraryItemTags(id: $id, tags: $tags) { id } }';
const CREATE_DASHBOARD = 'mutation ($i: CreateDashboardInput!) { createDashboard(input: $i) { id title } }';
const UPDATE_DASHBOARD = 'mutation ($id: ID!, $i: UpdateDashboardInput!) { updateDashboard(id: $id, input: $i) { id } }';
const ADD_ITEM = `mutation ($d: ID!, $i: ID!, $p: PositionInput!) {
  addItemToDashboard(dashboardId: $d, libraryItemId: $i, position: $p) { id }
}`;
const ADD_FILTER = `mutation ($d: ID!, $i: AddDashboardFilterInput!) {
  addDashboardFilter(dashboardId: $d, input: $i) { id filters { id label dimension } }
}`;
const CREATE_AUTOPILOT = 'mutation ($i: CreateAutopilotInput!) { createAutopilot(input: $i) { id name } }';

const read = (dir, name) => JSON.parse(fs.readFileSync(path.join(dir, name), 'utf8'));
const pick = (source, keys) =>
  Object.fromEntries(keys.filter((k) => source[k] !== null && source[k] !== undefined).map((k) => [k, source[k]]));

/** The `SaveToLibraryInput` fields, which the export is a superset of. */
const ITEM_FIELDS = ['title', 'description', 'type', 'visibility', 'visualizationSpec', 'kind', 'query',
  'parameterValues', 'customSql', 'customBindings', 'datasourceId', 'aiContext', 'modelId',
  'sourceThreadId', 'sourceMessageId', 'folderId'];

(async () => {
  const base = process.env.APP_BASE;
  const dir = path.resolve(process.env.IN || 'configuration-backup');
  if (!base) throw new Error('APP_BASE is required');
  if (process.env.CONFIRM_TARGET !== base) {
    throw new Error(`CONFIRM_TARGET must repeat APP_BASE exactly; got "${process.env.CONFIRM_TARGET ?? ''}"`);
  }
  if (base.includes(FROZEN) && process.env.ALLOW_LIVE_STAND !== 'yes') {
    throw new Error(`${FROZEN} is the stand this export came from and it is configured by hand; refusing`);
  }

  const manifest = read(dir, 'MANIFEST.json');
  console.log(`restoring an export of ${manifest.stand} taken ${manifest.exportedAt}`);
  console.log(`            into ${base}`);

  const cookie = await signIn({
    base,
    username: process.env.APP_USER,
    password: fs.readFileSync(process.env.PASS_FILE, 'utf8').trim(),
  });
  const q = (query, variables) => graphql({ base, cookie, query, variables });

  // 1. The semantic model, per tenant, through the product's own import. A
  //    rebuilt stand has different tenant ids, so each file is matched to the
  //    tenant of the same NAME rather than to the id it was exported under.
  const { adminTenants } = await q('{ adminTenants { id name } }');
  for (const tenant of manifest.tenants) {
    const document = read(dir, tenant.file);
    if ((document.datasets ?? []).length === 0) {
      console.log(`  ${tenant.name}: empty model, nothing to import`);
      continue;
    }
    const target = adminTenants.find((one) => one.name === tenant.name);
    if (!target) throw new Error(`no tenant named "${tenant.name}" on ${base}`);
    const result = await q(IMPORT_MODEL, { doc: document, mode: 'replace', t: target.id });
    console.log(`  ${tenant.name}: ${JSON.stringify(result.adminSemanticModelImport)}`);
  }

  // 2. The library items. The new ids are remembered, because a dashboard's
  //    layout refers to items by id and those ids have just changed.
  const idByOldId = new Map();
  for (const item of read(dir, 'library-items.json')) {
    const created = (await q(SAVE_ITEM, { i: pick(item, ITEM_FIELDS) })).saveToLibrary;
    idByOldId.set(item.id, created.id);
    if (item.tags?.length) await q(SET_TAGS, { id: created.id, tags: item.tags });
    console.log(`  item ${created.title} → ${created.id}`);
  }

  // 3. The dashboards. `addItemToDashboard` places a widget and mints a fresh
  //    layout item id, so the exported layout is rewritten onto the new ids
  //    rather than replayed: it is the geometry that is being restored, not the
  //    identifiers.
  for (const dashboard of read(dir, 'dashboards.json')) {
    const created = (await q(CREATE_DASHBOARD, {
      i: pick(dashboard, ['title', 'description', 'about', 'visibility', 'tags']),
    })).createDashboard;
    const layout = Array.isArray(dashboard.layout) ? dashboard.layout : (dashboard.layout?.items ?? []);
    for (const cell of layout) {
      const itemId = idByOldId.get(cell.libraryItemId ?? cell.itemId);
      if (!itemId) {
        console.log(`    ! layout cell ${JSON.stringify(cell).slice(0, 80)} names an item not in the export`);
        continue;
      }
      await q(ADD_ITEM, {
        d: created.id, i: itemId,
        p: { x: cell.x ?? 0, y: cell.y ?? 0, w: cell.w ?? 6, h: cell.h ?? 6 },
      });
    }
    for (const filter of dashboard.filters ?? []) {
      await q(ADD_FILTER, {
        d: created.id,
        i: pick(filter, ['dimension', 'hierarchy', 'label', 'type', 'defaultValue', 'hidden']),
      });
    }
    await q(UPDATE_DASHBOARD, {
      id: created.id,
      i: pick(dashboard, ['refreshTtlSeconds', 'autoRefreshOnData', 'periodAnchor']),
    }).catch((error) => console.log(`    ! updateDashboard: ${error.message.slice(0, 160)}`));
    console.log(`  dashboard ${created.title} → ${created.id} (${layout.length} widgets)`);
  }

  // 4. The autopilots. `harness/autopilots.js` creates the stand's two from
  //    their templates and is the better path when the templates are current;
  //    this replays whatever the export captured, templates included.
  for (const autopilot of read(dir, 'autopilots.json')) {
    const input = pick(autopilot, ['name', 'promptTemplate', 'modelId', 'enabled', 'cronExpression',
      'visibility', 'signalMode', 'signalBlock', 'allowedMcpTools', 'parameters']);
    input.eventSubscriptions = (autopilot.eventSubscriptions ?? []).map((one) =>
      pick(one, ['eventId', 'daysOfWeek', 'signalFilter']));
    const created = (await q(CREATE_AUTOPILOT, { i: input })).createAutopilot;
    console.log(`  autopilot ${created.name} → ${created.id}`);
  }

  console.log('done — now check the dashboards render, and re-register the MCP server (harness/mcpreg.js)');
})().catch((e) => { console.log('FAILED', e.message); process.exit(1); });
