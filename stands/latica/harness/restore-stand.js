// Re-apply an export from `export-stand.js` to a REBUILT stand.
//
// This script mutates. It exists because the semantic model, the dashboards, the
// library items, the autopilots and the clinic's MCP registration live in the
// deployment's own PostgreSQL, and a re-created deployment starts with none of
// them — so without this the rebuild means somebody sitting with the agent for
// an afternoon again.
//
// THREE THINGS IT LEARNED THE HARD WAY, on a throwaway 5.0.3 stand on
// 2026-11-25 (SUP-917 → SUP-939). All three are the same mistake in different
// places: it wrote before it knew.
//
//  1. **It reconciles; it does not create.** Every list here is matched against
//     what the target already holds, by the stable name a person chose, and only
//     the difference is written. The old version created unconditionally, always
//     stopped at the autopilots, and a re-run — which is what anybody would do —
//     created everything again: 15 library items became 37 and 2 dashboards
//     became 4. A second run of this script writes nothing. `reconcile.js` is
//     the matching, `tests/reconcile.test.js` pins it.
//  2. **It registers the MCP server, before the autopilots.** `createAutopilot`
//     refuses an `allowedMcpTools` entry it cannot resolve
//     (`AUTOPILOT_MCP_TOOL_UNKNOWN`), and `mcp_scheduling_schedule_recall` is
//     resolvable only once the clinic's tool server is registered AND switched
//     on, because enabling is what caches its tool list. Both autopilots were
//     lost 3 times out of 3 for want of that one step.
//  3. **It validates everything before it writes anything.** Every precondition
//     below is a read. On a failed precondition nothing has been changed, which
//     is the only safe thing to hand somebody at that point. `DRY_RUN=yes`
//     stops after the plan.
//
// Two things it still does NOT restore, because no API offers them: a library
// item's version history, and a dashboard's saved versions. Each item is
// recreated at version 1 with its current definition.
//
//   APP_BASE=https://<the rebuilt stand> APP_USER=… PASS_FILE=… \
//   IN=../configuration-backup CONFIRM_TARGET=<the same APP_BASE> \
//   MCP_TOKEN_FILE=./clinic.token \
//   KC_BASE=https://<the rebuilt stand>/auth KC_PASS_FILE=./stand.internalSecret \
//   APP_HOST=<the stand host> RESEARCHER_PASS_FILE=./researcher.password \
//   node harness/restore-stand.js
//
//   … DRY_RUN=yes node harness/restore-stand.js    # validate and plan, write nothing
//   … PRUNE=yes   node harness/restore-stand.js    # also remove what the export does not account for
//
// The `KC_*` / `APP_HOST` / `RESEARCHER_PASS_FILE` group is for step 0, the
// researcher tenant: the listing can only declare three organisations, so the
// fourth is created by hand and a rebuild starts without it. Leave them unset
// and the restore still runs, and says loudly that the stand it produced has
// three tenants rather than four — unless the export carries a model for that
// tenant, in which case it refuses before writing, because importing it would
// be impossible and discovering that afterwards is worse.
//
// Step 0b gives that tenant its data sources, because the deployment's grounding
// Job grounds only its own. It reads the dataset cards from `CARDS`, which
// defaults to this repository's `datasets/` directory.
//
// `MCP_TOKEN_FILE` is the bearer token the clinic issued. A path, never an
// argument, and never in this repository. See `export-stand.js` for where it
// lives and why the export cannot carry it.
//
// `CONFIRM_TARGET` must repeat `APP_BASE` exactly, and the stand this export was
// taken FROM is refused by name: on 2026-11-23 its owner configured it by hand
// and said in so many words that it must not be disturbed. Overriding that needs
// `ALLOW_LIVE_STAND=yes` and a reason you can defend.
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');
const { signIn, graphql } = require('./applogin');
const C = require('./stand-config');
const R = require('./reconcile');

const FROZEN = 'analyst-stand.conf-apps.superprotocol.dev';

/** The researcher tenant, and the three aggregate releases its sources point at.
 *  Kept in step with `researcher-tenant.js` and `researcher-sources.js`, which
 *  own these defaults — this script only has to know them to CHECK them before
 *  it calls either script. */
const RESEARCH_TENANT = process.env.TENANT_NAME || 'Clinical Research Network';
const RELEASES = (process.env.SOURCES || 'cypressbay-release,harborpoint-release,ridgeline-release')
  .split(',').map((one) => one.trim()).filter(Boolean);
/** The same transformation `researcher-sources.js` and `ground.py` apply to a
 *  card name to get a data source id. Identical on purpose. */
const identifier = (name) => name.replace(/[^a-zA-Z0-9]+/g, '_').replace(/^_+|_+$/g, '').toLowerCase();

const IMPORT_MODEL = `mutation ($doc: JSON!, $mode: SemanticImportMode!, $t: String) {
  adminSemanticModelImport(document: $doc, mode: $mode, tenantId: $t) {
    created updated deleted calendar invalidatedItems
  }
}`;
const EVENTS = '{ availableEvents { id name systemKey } }';
const CREATE_FOLDER = 'mutation ($name: String!, $parentId: ID) { createLibraryFolder(name: $name, parentId: $parentId) { id name parentId } }';
const SAVE_ITEM = 'mutation ($i: SaveToLibraryInput!) { saveToLibrary(input: $i) { id title } }';
const UPDATE_ITEM = 'mutation ($id: ID!, $i: UpdateLibraryItemInput!) { updateLibraryItem(id: $id, input: $i) { id title } }';
const MOVE_ITEMS = 'mutation ($ids: [ID!]!, $folderId: ID) { moveLibraryItems(ids: $ids, folderId: $folderId) }';
const DELETE_ITEM = 'mutation ($id: ID!) { deleteLibraryItem(id: $id) }';
const CREATE_DASHBOARD = 'mutation ($i: CreateDashboardInput!) { createDashboard(input: $i) { id title } }';
const UPDATE_DASHBOARD = 'mutation ($id: ID!, $i: UpdateDashboardInput!) { updateDashboard(id: $id, input: $i) { id } }';
const DELETE_DASHBOARD = 'mutation ($id: ID!) { deleteDashboard(id: $id) }';
// `layout` comes back so the layout-item id this call minted can be picked out:
// a widget's filter binding is addressed by that id, and it is new every time.
const ADD_ITEM = `mutation ($d: ID!, $i: ID!, $p: PositionInput!) {
  addItemToDashboard(dashboardId: $d, libraryItemId: $i, position: $p) { id layout }
}`;
const REMOVE_ITEM = 'mutation ($d: ID!, $l: ID!) { removeItemFromDashboard(dashboardId: $d, layoutItemId: $l) { id } }';
const SET_LAYOUT = 'mutation ($id: ID!, $layout: JSON!) { updateDashboardLayout(id: $id, layout: $layout) { id } }';
const ADD_FILTER = `mutation ($d: ID!, $i: AddDashboardFilterInput!) {
  addDashboardFilter(dashboardId: $d, input: $i) { id filters { id label dimension hierarchy type order } }
}`;
const UPDATE_FILTER = `mutation ($d: ID!, $f: ID!, $i: UpdateDashboardFilterInput!) {
  updateDashboardFilter(dashboardId: $d, filterId: $f, input: $i) { id }
}`;
const REMOVE_FILTER = 'mutation ($d: ID!, $f: ID!) { removeDashboardFilter(dashboardId: $d, filterId: $f) { id } }';
const SET_BINDING = `mutation ($d: ID!, $l: ID!, $f: ID!, $p: String, $lv: String) {
  setWidgetFilterBinding(dashboardId: $d, layoutItemId: $l, filterId: $f, parameter: $p, level: $lv) { id }
}`;
const CREATE_AUTOPILOT = 'mutation ($i: CreateAutopilotInput!) { createAutopilot(input: $i) { id name } }';
const UPDATE_AUTOPILOT = 'mutation ($id: ID!, $i: UpdateAutopilotInput!) { updateAutopilot(id: $id, input: $i) { id name } }';
const DELETE_AUTOPILOT = 'mutation ($id: ID!) { deleteAutopilot(id: $id) }';
const CHECK_MCP = `mutation ($i: CheckMcpServerInputDTO!) {
  checkMcpServer(input: $i) { ok errorCode error tools { name boundName } }
}`;
const CREATE_MCP = 'mutation ($i: CreateMcpServerInputDTO!) { createMcpServer(input: $i) { id name slug url enabled } }';
const UPDATE_MCP = 'mutation ($i: UpdateMcpServerInputDTO!) { updateMcpServer(input: $i) { id slug url enabled } }';
const ENABLE_MCP = 'mutation ($id: ID!, $enabled: Boolean!) { setMcpServerEnabled(id: $id, enabled: $enabled) { id enabled } }';
const DELETE_MCP = 'mutation ($id: ID!) { deleteMcpServer(id: $id) }';

const read = (dir, name) => JSON.parse(fs.readFileSync(path.join(dir, name), 'utf8'));
const exists = (dir, name) => fs.existsSync(path.join(dir, name));
const pick = (source, keys) =>
  Object.fromEntries(keys.filter((k) => source[k] !== null && source[k] !== undefined).map((k) => [k, source[k]]));
const yes = (value) => String(value ?? '').toLowerCase() === 'yes';

/** Which of `fields` two shapes disagree on. Drives both the log line and the
 *  decision about whether a mutation is needed at all. */
const differing = (wanted, existing, fields) =>
  fields.filter((field) => R.signature(wanted[field]) !== R.signature(existing[field]));

/**
 * A patch that carries `null` where the export says null, rather than leaving
 * the field out.
 *
 * `pick` is right for a create — an absent field takes the product's own
 * default — and wrong for an update: a field somebody filled in that the export
 * has empty has to be CLEARED, and a patch that simply omits it leaves the
 * drift in place and reports success. That was a real bug, caught by the
 * "drifted item is corrected in place" test: an item whose `customSql` had been
 * edited stayed edited, because the export's value for it is null.
 */
const patchOf = (source, keys) => Object.fromEntries(keys.map((key) => [key, source[key] ?? null]));

(async () => {
  const base = process.env.APP_BASE;
  const dir = path.resolve(process.env.IN || 'configuration-backup');
  const dryRun = yes(process.env.DRY_RUN);
  const prune = yes(process.env.PRUNE);
  if (!base) throw new Error('APP_BASE is required');
  if (process.env.CONFIRM_TARGET !== base) {
    throw new Error(`CONFIRM_TARGET must repeat APP_BASE exactly; got "${process.env.CONFIRM_TARGET ?? ''}"`);
  }
  if (base.includes(FROZEN) && !yes(process.env.ALLOW_LIVE_STAND)) {
    throw new Error(`${FROZEN} is the stand this export came from and it is configured by hand; refusing`);
  }

  // =========================================================================
  // Reading the export. Nothing is written in this section or the next two.
  // =========================================================================
  const manifest = read(dir, 'MANIFEST.json');
  console.log(`restoring an export of ${manifest.stand} taken ${manifest.exportedAt}`);
  console.log(`            into ${base}${dryRun ? '  (DRY RUN — nothing will be written)' : ''}`);

  const problems = [];
  const refuse = (text) => problems.push(text);

  for (const file of ['library-items.json', 'library-folders.json', 'dashboards.json']) {
    if (!exists(dir, file)) refuse(`${file} is missing from ${dir}`);
  }
  for (const tenant of manifest.tenants ?? []) {
    if (!exists(dir, tenant.file)) refuse(`${tenant.file} is missing from ${dir} (the model of "${tenant.name}")`);
  }
  if ((manifest.counts?.autopilots ?? 0) > 0 && !exists(dir, 'autopilots.json')) {
    refuse(`the manifest counts ${manifest.counts.autopilots} autopilot(s) and autopilots.json is missing`);
  }
  if ((manifest.counts?.mcpServers ?? 0) > 0 && !exists(dir, 'mcp-servers.json')) {
    refuse(`the manifest counts ${manifest.counts.mcpServers} tool server(s) and mcp-servers.json is missing`);
  }
  if (problems.length > 0) {
    for (const problem of problems) console.log(`  ! ${problem}`);
    throw new Error('the export is incomplete; nothing has been changed');
  }

  const wantedItems = read(dir, 'library-items.json');
  const wantedFolders = read(dir, 'library-folders.json');
  const wantedBoards = read(dir, 'dashboards.json');
  const wantedPilots = exists(dir, 'autopilots.json') ? read(dir, 'autopilots.json') : [];
  // A global row is rendered from the listing's parameters, not registered
  // through the API: there is nothing here that could put one back.
  const wantedMcpAll = exists(dir, 'mcp-servers.json') ? read(dir, 'mcp-servers.json') : [];
  const wantedMcp = wantedMcpAll.filter((one) => !one.global);

  // A manifest whose counts disagree with its own files is a truncated backup,
  // and the restore is the last place that can still say so.
  const declared = manifest.counts ?? {};
  const measured = {
    libraryItems: wantedItems.length,
    libraryFolders: wantedFolders.length,
    dashboards: wantedBoards.length,
    autopilots: wantedPilots.length,
    mcpServers: wantedMcp.length,
  };
  for (const [name, count] of Object.entries(measured)) {
    if (declared[name] !== undefined && declared[name] !== count) {
      refuse(`the manifest counts ${declared[name]} ${name} and the files hold ${count}`);
    }
  }
  // Everything checked so far is local, so it is answered before the stand is
  // so much as signed in to: an export that cannot be restored is not worth
  // opening a session for.
  if (problems.length > 0) {
    for (const problem of problems) console.log(`  ! ${problem}`);
    throw new Error('the export disagrees with its own manifest; nothing has been changed');
  }

  // =========================================================================
  // Reading the target. Every call below is a query, and `q` retries a gateway
  // failure because a read costs nothing to repeat. The writers further down
  // deliberately do not retry: a create that timed out may have landed.
  // =========================================================================
  const cookie = await signIn({
    base,
    username: process.env.APP_USER,
    password: fs.readFileSync(process.env.PASS_FILE, 'utf8').trim(),
  });
  const write = (query, variables) => graphql({ base, cookie, query, variables });
  const q = async (query, variables) => {
    let last = null;
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        return await graphql({ base, cookie, query, variables });
      } catch (error) {
        last = error;
        if (!/\b50[0-9]\b/.test(error.message)) throw error;
        console.log(`  gateway ${error.message.slice(0, 40).replace(/\s+/g, ' ')} — retrying (${attempt})`);
        await new Promise((resolve) => { setTimeout(resolve, 4000 * attempt); });
      }
    }
    throw last;
  };

  const live = {
    tenants: (await q(C.TENANTS)).adminTenants,
    library: await q(C.LIBRARY),
    dashboards: (await q(C.DASHBOARDS)).dashboards,
    events: (await q(EVENTS)).availableEvents,
    sources: null,
    autopilots: [],
    mcp: [],
  };
  try {
    // An installation-administrator read. A stand where it is refused is a
    // stand where step 0b's precondition cannot be checked, which is said
    // below rather than guessed at.
    live.sources = (await q(C.DATA_SOURCES)).adminDataSources.items;
  } catch (error) {
    console.log(`  - the data sources cannot be read as ${process.env.APP_USER}: ${error.message.slice(0, 120)}`);
  }
  try {
    live.autopilots = (await q(C.AUTOPILOTS)).autopilots;
  } catch (error) {
    if (wantedPilots.length > 0) refuse(`autopilots cannot be read on this stand: ${error.message.slice(0, 160)}`);
  }
  try {
    live.mcp = (await q(C.MCP)).mcpServers;
  } catch (error) {
    if (wantedMcp.length > 0) refuse(`mcp servers cannot be read on this stand: ${error.message.slice(0, 160)}`);
  }

  // =========================================================================
  // The preconditions. All of them, before the first write — so that a stand
  // that cannot be restored is a stand nothing was done to.
  // =========================================================================
  const kcReady = Boolean(process.env.KC_BASE && process.env.KC_PASS_FILE && process.env.APP_HOST);
  const researchPresent = live.tenants.some((one) => one.name === RESEARCH_TENANT);

  // 1. Every tenant whose model has to be imported must exist, or be the one
  //    step 0 is about to create.
  for (const tenant of manifest.tenants ?? []) {
    const model = read(dir, tenant.file);
    if ((model.datasets ?? []).length === 0) continue;
    if (live.tenants.some((one) => one.name === tenant.name)) continue;
    if (tenant.name === RESEARCH_TENANT && kcReady) continue;
    refuse(tenant.name === RESEARCH_TENANT
      ? `the export carries a semantic model for "${RESEARCH_TENANT}" and that tenant does not exist here. `
        + 'Set KC_BASE, KC_PASS_FILE and APP_HOST so step 0 can create it'
      : `no tenant named "${tenant.name}" on ${base}, and nothing here would create one — `
        + `its model (${tenant.counts?.datasets ?? '?'} datasets) could not be imported`);
  }

  // 2. Keycloak's admin API, reached now rather than discovered to be
  //    unreachable after the model is in. The realm's `internalSecret` is not
  //    published as a deployment output, so a wrong one is an easy mistake and
  //    an expensive place to make it.
  if (kcReady) {
    if (!fs.existsSync(process.env.KC_PASS_FILE)) {
      refuse(`KC_PASS_FILE ${process.env.KC_PASS_FILE} does not exist`);
    } else if (!(await keycloakAnswers())) {
      refuse(`${process.env.KC_BASE} did not mint an admin token from KC_PASS_FILE — step 0 would fail`);
    }
  }

  // 3. Step 0b needs the three release cards on disk AND mounted on this
  //    deployment: it registers the researcher's source against the PROVIDER the
  //    grounding Job made for the clinic tenant, so a card that is not mounted
  //    is a source it cannot point at. This is the precondition the old script
  //    discovered by 404-ing on `adminSchemaRefresh` — after it had already
  //    created the tenant.
  const cards = process.env.CARDS || path.join(__dirname, '..', '..', '..', 'datasets');
  const groundResearcher = (kcReady || researchPresent) && fs.existsSync(cards);
  if (kcReady || researchPresent) {
    if (!fs.existsSync(cards)) {
      console.log(`  - no dataset cards at ${cards}: step 0b will be skipped and the researcher tenant`);
      console.log('    will hold no data. Set CARDS to the repository\'s datasets directory to include it.');
    } else {
      for (const release of RELEASES) {
        if (!fs.existsSync(path.join(cards, release, 'data.yaml'))) {
          refuse(`no dataset card at ${path.join(cards, release, 'data.yaml')} — set CARDS, or SOURCES to what is mounted`);
        }
        if (live.sources === null) {
          refuse('the data sources could not be read, so whether the release cards are mounted cannot be '
            + 'checked. Sign in as the installation administrator, or unset KC_BASE to skip the '
            + 'researcher tenant altogether');
          break;
        }
        if (!live.sources.some((one) => one.datasourceId === identifier(release))) {
          refuse(`${identifier(release)} is mounted nowhere on this deployment, so the researcher tenant `
            + 'cannot be given it. Mount the release cards on the deployment first, or set SOURCES');
        }
      }
    }
  }

  // 4. The tool servers, and the autopilots that cannot be created without
  //    them. The probe is `checkMcpServer` with a slug and a URL and no `id`,
  //    which is the one shape that persists nothing: the service only writes a
  //    tool cache or a `lastError` when it was handed a saved row.
  const tokenFiles = new Map();
  const probedTools = new Map();
  for (const server of wantedMcp) {
    const onTarget = live.mcp.find((one) => one.slug === server.slug && !one.global);
    const file = tokenFileFor(server, wantedMcp);
    if (server.hasAuth) {
      if (!file) {
        refuse(`${server.slug} carries an ${(server.authHeaderNames ?? ['Authorization']).join('/')} header and no `
          + `token file was given. Set MCP_TOKEN_FILE (or MCP_TOKEN_FILE_${server.slug.toUpperCase()}) to the path of `
          + 'the token the clinic issued');
        continue;
      }
      if (!fs.existsSync(file)) {
        refuse(`${server.slug}: no token file at ${file}`);
        continue;
      }
      if (fs.readFileSync(file, 'utf8').trim() === '') {
        refuse(`${server.slug}: the token file ${file} is empty`);
        continue;
      }
      tokenFiles.set(server.slug, file);
    }
    const url = process.env.MCP_URL || server.url;
    const probe = await probeMcp(server, url, tokenFiles.get(server.slug));
    if (!probe.ok) {
      refuse(`${server.slug} at ${url} did not answer a tool list: ${probe.errorCode ?? ''} ${probe.error ?? ''}`.trim()
        + (onTarget ? '' : '. A rebuilt deployment attests a NEW digest, and the clinic refuses a caller whose'
          + ' digest it has not approved (403, attestation_digest_mismatch in its journal) — re-point'
          + ' `approvedDigest` in the receiver\'s grants.json at this deployment\'s evidence digest first'));
      continue;
    }
    probedTools.set(server.slug, probe.tools.map((one) => one.boundName));
  }

  // The tools the autopilots name must be resolvable, from a server this run
  // will have registered or from one already here. An autopilot created without
  // them is not created at all.
  const resolvable = new Set([
    ...[...probedTools.values()].flat(),
    ...live.mcp.flatMap((one) => (one.tools ?? []).map((tool) => tool.boundName)),
  ]);
  for (const tool of new Set(wantedPilots.flatMap((one) => one.allowedMcpTools ?? []))) {
    if (!resolvable.has(tool)) {
      refuse(`no tool server here resolves ${tool}, which an autopilot allows — `
        + '`createAutopilot` would refuse it with AUTOPILOT_MCP_TOOL_UNKNOWN. '
        + 'Register the server that serves it (mcp-servers.json, harness/mcpreg.js) before restoring');
    }
  }

  // 5. Every event an exported subscription names must exist on this
  //    installation: the ids are new, the names are what they are matched by.
  const eventIdByName = new Map(live.events.map((one) => [one.name, one.id]));
  const subscribedNames = [
    ...wantedBoards.flatMap((one) => (one.eventSubscriptions ?? []).map((sub) => sub.eventName)),
    ...wantedPilots.flatMap((one) => (one.eventSubscriptions ?? []).map((sub) => sub.eventName)),
  ];
  for (const name of new Set(subscribedNames.filter(Boolean))) {
    if (!eventIdByName.has(name)) {
      refuse(`an exported subscription names the event "${name}", which this installation does not publish`);
    }
  }

  if (problems.length > 0) {
    console.log('\nPRECONDITIONS NOT MET — nothing has been changed:');
    for (const problem of problems) console.log(`  ! ${problem}`);
    process.exit(1);
  }

  // =========================================================================
  // The plan. Computed from the same reads, so `DRY_RUN=yes` and a real run
  // agree about what there is to do.
  // =========================================================================
  const livePaths = C.folderPaths(live.library.libraryFolders);
  const wantedPaths = C.folderPaths(wantedFolders);
  const folderPlan = R.match(
    wantedFolders.map((one) => C.folderShape(one, wantedPaths)),
    live.library.libraryFolders.map((one) => C.folderShape(one, livePaths)),
    { keyFor: R.folderKey, same: R.folderSame },
  );
  const itemPlan = R.match(
    wantedItems.map((one) => C.libraryShape(one, wantedPaths)),
    live.library.libraryItems.map((one) => C.libraryShape(one, livePaths)),
    { keyFor: R.libraryKey, same: R.librarySame },
  );
  const boardPlan = R.match(
    wantedBoards.map(C.dashboardShape),
    live.dashboards.map(C.dashboardShape),
    { keyFor: R.dashboardKey, same: R.dashboardSame },
  );
  const mcpPlan = R.match(
    wantedMcp.map(C.mcpShape),
    live.mcp.filter((one) => !one.global).map(C.mcpShape),
    { keyFor: R.mcpKey, same: R.mcpSame },
  );
  const pilotPlan = R.match(
    wantedPilots.map(C.autopilotShape),
    live.autopilots.map(C.autopilotShape),
    { keyFor: R.autopilotKey, same: R.autopilotSame },
  );

  console.log('\nplan');
  for (const [name, plan] of Object.entries({
    folders: folderPlan, 'library items': itemPlan, dashboards: boardPlan,
    'tool servers': mcpPlan, autopilots: pilotPlan,
  })) {
    const { unchanged, update, create, extra } = R.counts(plan);
    console.log(`  ${name.padEnd(14)} ${create} to create, ${update} to update, ${unchanged} already right`
      + `, ${extra} here that the export does not account for${extra > 0 && !prune ? ' (left alone)' : ''}`);
  }
  if (dryRun) {
    console.log('\nDRY RUN — nothing was written.');
    return;
  }

  // =========================================================================
  // From here on it writes.
  // =========================================================================
  const done = { created: 0, updated: 0, unchanged: 0, removed: 0, manual: [] };

  // 0. The researcher tenant, FIRST and in Keycloak rather than in the app. The
  //    Conversational Analyst listing carries exactly three organisation slots —
  //    `organizations` is a fixed three-entry list patched by numeric index — so
  //    the fourth has never been part of any render and a rebuilt stand has no
  //    trace of it. It has to exist before step 1, because the semantic model is
  //    imported per tenant and `adminTenants` only lists a tenant the realm
  //    already has. `researcher-tenant.js` is create-unless-present at every
  //    step, so running it against a stand that has the tenant is a no-op.
  if (kcReady) {
    console.log('\n0. the researcher tenant');
    const result = spawnSync(process.execPath, [path.join(__dirname, 'researcher-tenant.js')], {
      stdio: 'inherit', env: process.env,
    });
    if (result.status !== 0) {
      throw new Error('researcher-tenant.js failed; the fourth tenant is not in place and step 1 would skip its model');
    }
  } else {
    console.log('\n0. the researcher tenant: SKIPPED — KC_BASE, KC_PASS_FILE and APP_HOST were not all set.');
    console.log('   This stand will have THREE tenants, not four. Run harness/researcher-tenant.js');
    console.log('   before relying on anything that needs the researcher, and re-run this script.');
  }

  // 0b. And its sources. The deployment's grounding Job registers every mounted
  //     card into ONE tenant — the deployment's `primaryOrgAlias`, an attested
  //     parameter — so nothing grounds the tenant that was created by hand. This
  //     is the same three things `ground.py` does (register, read the catalogue,
  //     annotate from the card), for that tenant only, and it is itself
  //     create-unless-present.
  //
  //     After step 0 and before the model, in that order: a semantic model binds
  //     to a data source, and importing one into a tenant whose sources are not
  //     registered yet binds it to nothing. Its preconditions — the cards on
  //     disk and the releases mounted — were checked above.
  if (groundResearcher) {
    console.log('\n0b. the researcher tenant\'s sources');
    const result = spawnSync(process.execPath, [path.join(__dirname, 'researcher-sources.js')], {
      stdio: 'inherit', env: { ...process.env, CARDS: cards },
    });
    if (result.status !== 0) {
      throw new Error('researcher-sources.js failed; its tenant would hold no data and its semantic model '
        + 'would bind to nothing. Nothing else has been written yet');
    }
  }

  // 1. The semantic model, per tenant, through the product's own import. A
  //    rebuilt stand has different tenant ids, so each file is matched to the
  //    tenant of the same NAME rather than to the id it was exported under. The
  //    live model is read first: `replace` is idempotent in effect, but a model
  //    that already matches is not re-imported, so a second run of this script
  //    really does write nothing.
  console.log('\n1. the semantic model');
  const tenantsNow = (await q(C.TENANTS)).adminTenants;
  for (const tenant of manifest.tenants ?? []) {
    const document = read(dir, tenant.file);
    if ((document.datasets ?? []).length === 0) {
      console.log(`  ${tenant.name}: empty model, nothing to import`);
      continue;
    }
    const target = tenantsNow.find((one) => one.name === tenant.name);
    if (!target) throw new Error(`no tenant named "${tenant.name}" on ${base}`);
    const current = (await q(C.SEMANTIC, { t: target.id })).adminSemanticModelExport;
    if (R.signature(current) === R.signature(document)) {
      console.log(`  ${tenant.name}: already this model`);
      done.unchanged += 1;
      continue;
    }
    const result = await write(IMPORT_MODEL, { doc: document, mode: 'replace', t: target.id });
    console.log(`  ${tenant.name}: ${JSON.stringify(result.adminSemanticModelImport)}`);
    done.updated += 1;
  }

  // 2. The library. Folders first — an item names its folder by id, and those
  //    ids are new — then the items, whose new ids are remembered because a
  //    dashboard's layout refers to items by id.
  console.log('\n2. the library');
  const folderIdByPath = new Map(
    live.library.libraryFolders.map((one) => [livePaths.get(one.id) ?? one.name, one.id]),
  );
  for (const folder of folderPlan.create) {
    const parentPath = folder.folderPath.includes('/')
      ? folder.folderPath.slice(0, folder.folderPath.lastIndexOf('/')) : null;
    const created = (await write(CREATE_FOLDER, {
      name: folder.name,
      parentId: parentPath ? (folderIdByPath.get(parentPath) ?? null) : null,
    })).createLibraryFolder;
    folderIdByPath.set(folder.folderPath, created.id);
    console.log(`  folder ${folder.folderPath} → ${created.id}`);
    done.created += 1;
  }
  done.unchanged += folderPlan.unchanged.length;

  /** The exported item's id → the id it has on the target. The one mapping a
   *  dashboard layout needs, and the only one precise enough: two items here
   *  share a title, so "the item called X" would not identify either. */
  const itemIdByOldId = new Map();

  for (const { wanted, existing } of itemPlan.unchanged) {
    itemIdByOldId.set(wanted.source.id, existing.source.id);
    done.unchanged += 1;
  }
  for (const { wanted, existing } of itemPlan.update) {
    const fields = differing(wanted, existing, R.LIBRARY_FIELDS);
    // `UpdateLibraryItemInput` has no field for these four, and `saveToLibrary`
    // would make a second item rather than correct this one. Named rather than
    // swallowed: a difference nobody is told about is the one that surfaces on a
    // projector.
    const unsettable = fields.filter((field) => ['parameterValues', 'aiContext', 'modelId', 'type'].includes(field));
    const settable = ['title', 'description', 'visibility', 'tags', 'kind', 'query',
      'customSql', 'customBindings', 'visualizationSpec', 'datasourceId'];
    const changed = fields.filter((field) => settable.includes(field));
    if (changed.length > 0) {
      await write(UPDATE_ITEM, {
        id: existing.source.id,
        i: {
          ...patchOf(wanted, changed),
          changeDescription: `restored from the export of ${manifest.exportedAt}`,
        },
      });
    }
    if (fields.includes('folderPath')) {
      await write(MOVE_ITEMS, {
        ids: [existing.source.id],
        folderId: wanted.folderPath ? (folderIdByPath.get(wanted.folderPath) ?? null) : null,
      });
    }
    itemIdByOldId.set(wanted.source.id, existing.source.id);
    console.log(`  item ${wanted.title}: updated (${fields.join(', ')})`);
    if (unsettable.length > 0) {
      done.manual.push(`library item "${wanted.title}": ${unsettable.join(', ')} cannot be set through `
        + 'updateLibraryItem — set it by hand');
      console.log(`    ! ${unsettable.join(', ')} has no field on UpdateLibraryItemInput; set it by hand`);
    }
    done.updated += 1;
  }
  for (const wanted of itemPlan.create) {
    const input = pick(wanted, ['title', 'description', 'type', 'visibility', 'visualizationSpec', 'kind',
      'query', 'parameterValues', 'customSql', 'customBindings', 'datasourceId', 'aiContext', 'modelId']);
    input.sourceThreadId = wanted.source.sourceThreadId ?? undefined;
    input.sourceMessageId = wanted.source.sourceMessageId ?? undefined;
    if (wanted.tags.length > 0) input.tags = wanted.tags;
    if (wanted.folderPath) input.folderId = folderIdByPath.get(wanted.folderPath) ?? null;
    const created = (await write(SAVE_ITEM, { i: pick(input, Object.keys(input)) })).saveToLibrary;
    itemIdByOldId.set(wanted.source.id, created.id);
    console.log(`  item ${created.title} → ${created.id}`);
    done.created += 1;
  }
  for (const extra of itemPlan.extra) {
    if (!prune) {
      console.log(`  - ${extra.title} (${extra.source.id}) is here and not in the export; left alone`);
      continue;
    }
    await write(DELETE_ITEM, { id: extra.source.id });
    console.log(`  - ${extra.title} removed`);
    done.removed += 1;
  }

  // 3. The dashboards. Every identifier in a dashboard is new on a rebuild — the
  //    library item ids changed in step 2, `addItemToDashboard` mints a fresh
  //    layout-item id, and `addDashboardFilter` mints a fresh filter id — so
  //    each is restored by MAPPING old to new rather than by replaying the
  //    exported id. What is replayed, and what is not, is listed in
  //    `configuration-backup/README.md`; keep the two in step.
  console.log('\n3. the dashboards');
  for (const wanted of boardPlan.create) {
    const created = (await write(CREATE_DASHBOARD, {
      i: pick(wanted, ['title', 'description', 'about', 'visibility', 'tags']),
    })).createDashboard;
    console.log(`  dashboard ${created.title} → ${created.id}`);
    done.created += 1;
    // A board that has just been created is handled exactly like one that was
    // already here, against an empty "existing" — one code path, so the refresh
    // settings `CreateDashboardInput` has no field for are applied here rather
    // than only on some later run.
    await applyBoard(wanted, {
      id: created.id, layout: { items: [] }, filters: [], widgetFilterBindings: [], eventSubscriptions: [],
    });
  }
  for (const { wanted, existing } of [...boardPlan.update, ...boardPlan.unchanged]) {
    await applyBoard(wanted, existing.source);
  }
  for (const extra of boardPlan.extra) {
    if (!prune) {
      console.log(`  - dashboard ${extra.title} (${extra.source.id}) is here and not in the export; left alone`);
      continue;
    }
    await write(DELETE_DASHBOARD, { id: extra.source.id });
    console.log(`  - dashboard ${extra.title} removed`);
    done.removed += 1;
  }

  // 4. The tool servers — BEFORE the autopilots, which is the whole point.
  //    `createMcpServer` stores no tool list; ENABLING is what reads the tools
  //    and caches them, and the cache is what `createAutopilot` resolves an
  //    `allowedMcpTools` entry against. So every row ends with an enable, and
  //    the result is read back rather than assumed.
  console.log('\n4. the tool servers');
  for (const wanted of mcpPlan.create) {
    const headers = tokenFiles.has(wanted.slug)
      ? [{ name: (wanted.authHeaderNames[0] ?? 'Authorization'), value: `Bearer ${fs.readFileSync(tokenFiles.get(wanted.slug), 'utf8').trim()}` }]
      : undefined;
    const created = (await write(CREATE_MCP, {
      i: {
        name: wanted.name,
        slug: wanted.slug,
        url: process.env.MCP_URL || wanted.url,
        description: wanted.description ?? undefined,
        enabled: false,
        ...(headers ? { authHeaders: headers } : {}),
      },
    })).createMcpServer;
    await write(ENABLE_MCP, { id: created.id, enabled: wanted.enabled !== false });
    console.log(`  ${created.slug} → ${created.id}, enabled=${wanted.enabled !== false}`);
    done.created += 1;
  }
  for (const { wanted, existing } of mcpPlan.update) {
    const fields = differing(wanted, existing, R.MCP_FIELDS);
    const patch = { id: existing.source.id };
    if (fields.includes('name')) patch.name = wanted.name;
    if (fields.includes('url')) patch.url = process.env.MCP_URL || wanted.url;
    if (fields.includes('description')) patch.description = wanted.description ?? null;
    // The token is re-sent only when this row has no credential, or when it was
    // asked for. `update` drops the tool cache whenever `authHeaders` is
    // present — a cache read with another token is stale by definition — and a
    // dropped cache is an autopilot allowlist that cannot be saved until the
    // row is enabled again. So a restore that re-sent it every run would not be
    // a no-op, it would be a cache invalidation on a timer.
    const resendToken = tokenFiles.has(wanted.slug)
      && (!existing.hasAuth || yes(process.env.MCP_REFRESH_TOKEN));
    if (resendToken) {
      patch.authHeaders = [{
        name: wanted.authHeaderNames[0] ?? 'Authorization',
        value: `Bearer ${fs.readFileSync(tokenFiles.get(wanted.slug), 'utf8').trim()}`,
      }];
    }
    if (Object.keys(patch).length > 1) await write(UPDATE_MCP, { i: patch });
    if (fields.includes('enabled') || patch.authHeaders) {
      await write(ENABLE_MCP, { id: existing.source.id, enabled: wanted.enabled !== false });
    }
    console.log(`  ${wanted.slug}: updated (${fields.join(', ') || 'credential'})`);
    done.updated += 1;
  }
  done.unchanged += mcpPlan.unchanged.length;
  for (const extra of mcpPlan.extra) {
    if (!prune) {
      console.log(`  - ${extra.slug} (${extra.source.id}) is here and not in the export; left alone`);
      continue;
    }
    await write(DELETE_MCP, { id: extra.source.id });
    console.log(`  - ${extra.slug} removed`);
    done.removed += 1;
  }

  // The cache, read back. An autopilot that names a tool nothing resolves is
  // refused outright, so this is checked before step 5 rather than discovered
  // by it — and at this point the autopilots are the only thing left to do, so
  // stopping here leaves a stand that is otherwise whole.
  if (wantedPilots.some((one) => (one.allowedMcpTools ?? []).length > 0)) {
    const after = (await q(C.MCP)).mcpServers;
    const bound = new Set(after.flatMap((one) => (one.tools ?? []).map((tool) => tool.boundName)));
    const missing = [...new Set(wantedPilots.flatMap((one) => one.allowedMcpTools ?? []))]
      .filter((tool) => !bound.has(tool));
    if (missing.length > 0) {
      for (const server of after) {
        console.log(`  ${server.slug} enabled=${server.enabled} tools=${server.toolCount} lastError=${server.lastError ?? '—'}`);
      }
      throw new Error(`the tool server(s) are registered but ${missing.join(', ')} is still not resolvable; `
        + 'the autopilots were NOT created. Everything before this step is in place');
    }
  }

  // 5. The autopilots. `harness/autopilots.js` creates the stand's two from
  //    their templates and is the better path when the templates are current;
  //    this replays whatever the export captured, templates included.
  console.log('\n5. the autopilots');
  const subscriptionsFor = (autopilot) => (autopilot.source.eventSubscriptions ?? []).map((one) => ({
    eventId: eventIdByName.get(one.eventName),
    ...(one.daysOfWeek ? { daysOfWeek: one.daysOfWeek } : {}),
    ...(one.signalFilter ? { signalFilter: cleanFilter(one.signalFilter) } : {}),
  }));
  for (const wanted of pilotPlan.create) {
    const input = pick(wanted, ['name', 'promptTemplate', 'modelId', 'enabled', 'cronExpression',
      'visibility', 'signalMode', 'signalBlock', 'allowedMcpTools', 'parameters']);
    input.eventSubscriptions = subscriptionsFor(wanted);
    const created = (await write(CREATE_AUTOPILOT, { i: input })).createAutopilot;
    console.log(`  autopilot ${created.name} → ${created.id}`);
    done.created += 1;
  }
  for (const { wanted, existing } of pilotPlan.update) {
    const fields = differing(wanted, existing, R.AUTOPILOT_FIELDS);
    // The whole definition, nulls included: an autopilot whose schedule
    // somebody cleared, or filled in, has to end up with the export's schedule
    // and not with whichever of the two was not mentioned.
    const input = patchOf(wanted, ['name', 'promptTemplate', 'modelId', 'enabled', 'cronExpression',
      'visibility', 'signalMode', 'signalBlock', 'allowedMcpTools', 'parameters']);
    input.eventSubscriptions = subscriptionsFor(wanted);
    await write(UPDATE_AUTOPILOT, { id: existing.source.id, i: input });
    console.log(`  autopilot ${wanted.name}: updated (${fields.join(', ')})`);
    done.updated += 1;
  }
  done.unchanged += pilotPlan.unchanged.length;
  for (const extra of pilotPlan.extra) {
    if (!prune) {
      console.log(`  - autopilot ${extra.name} (${extra.source.id}) is here and not in the export; left alone`);
      continue;
    }
    await write(DELETE_AUTOPILOT, { id: extra.source.id });
    console.log(`  - autopilot ${extra.name} removed`);
    done.removed += 1;
  }

  console.log(`\ndone — ${done.created} created, ${done.updated} updated, ${done.unchanged} already right`
    + `, ${done.removed} removed`);
  for (const line of done.manual) console.log(`  ! by hand: ${line}`);
  console.log('Re-run with DRY_RUN=yes: it should report nothing to create and nothing to update.');
  console.log('Then check the dashboards render — a widget whose query names a code the import did not');
  console.log('create comes back empty rather than missing, and that is the one thing no script can assert.');

  // =========================================================================
  // The pieces the steps above are made of.
  // =========================================================================

  /** Does Keycloak's admin API mint a token from `KC_PASS_FILE`? The same call
   *  `researcher-tenant.js` opens with, made here so step 0 fails before the
   *  restore starts rather than after the model is in. A read: `admin-cli` with
   *  a password grant creates nothing. */
  async function keycloakAnswers() {
    try {
      const response = await fetch(
        `${process.env.KC_BASE.replace(/\/$/, '')}/realms/${process.env.KC_REALM || 'master'}/protocol/openid-connect/token`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: new URLSearchParams({
            client_id: 'admin-cli',
            grant_type: 'password',
            username: process.env.KC_ADMIN_USER || 'admin',
            password: fs.readFileSync(process.env.KC_PASS_FILE, 'utf8').trim(),
          }).toString(),
        },
      );
      const payload = await response.json().catch(() => null);
      return response.ok && Boolean(payload?.access_token);
    } catch {
      return false;
    }
  }

  /** `MCP_TOKEN_FILE_<SLUG>` first, then `MCP_TOKEN_FILE` — which is only
   *  unambiguous when the export carries one server that needs a credential. */
  function tokenFileFor(server, all) {
    const specific = process.env[`MCP_TOKEN_FILE_${server.slug.toUpperCase().replace(/[^A-Z0-9]/g, '_')}`];
    if (specific) return specific;
    const needing = all.filter((one) => one.hasAuth);
    return needing.length === 1 ? process.env.MCP_TOKEN_FILE : undefined;
  }

  /** Ask the tool server for its tools without saving anything: `checkMcpServer`
   *  with a slug and a URL and no `id` opens a connection, lists, and persists
   *  neither a cache nor a `lastError`. */
  async function probeMcp(server, url, tokenFile) {
    const input = { slug: server.slug, url };
    if (tokenFile) {
      input.authHeaders = [{
        name: (server.authHeaderNames ?? [])[0] ?? 'Authorization',
        value: `Bearer ${fs.readFileSync(tokenFile, 'utf8').trim()}`,
      }];
    }
    try {
      const result = (await q(CHECK_MCP, { i: input })).checkMcpServer;
      return { ...result, tools: result.tools ?? [] };
    } catch (error) {
      return { ok: false, errorCode: 'MCP_CHECK_FAILED', error: error.message.slice(0, 200), tools: [] };
    }
  }

  /** `AutopilotSignalFilterInput` has no `__typename` and no nulls to spare. */
  function cleanFilter(filter) {
    const out = pick(filter, ['autopilotIds', 'minPriority', 'codes']);
    if (filter.scope) out.scope = filter.scope.map((one) => pick(one, ['key', 'value']));
    return out;
  }

  /** One dashboard's own settings, then everything on it. */
  async function applyBoard(wanted, existing) {
    const fields = differing(wanted, C.dashboardShape(existing), R.DASHBOARD_FIELDS);
    if (fields.length > 0) {
      await write(UPDATE_DASHBOARD, { id: existing.id, i: patchOf(wanted, fields) });
      console.log(`  dashboard ${wanted.title}: settings set (${fields.join(', ')})`);
      done.updated += 1;
    } else {
      done.unchanged += 1;
    }
    await reconcileBoard(existing.id, wanted, existing);
  }

  /**
   * One dashboard's widgets, filters and bindings, reconciled against what the
   * board holds now. Called for a board this run created (with an empty
   * "existing") and for one that was already here, so there is one code path
   * and a second run of it writes nothing.
   */
  async function reconcileBoard(boardId, wanted, existing) {
    // Both sides are named by the item's id ON THE TARGET: for an exported cell
    // that is what step 2 recorded, for a live cell it is the id it already
    // carries.
    const wantedCells = C.layoutItemsOf(wanted.source.layout)
      .map((cell) => C.widgetShape(cell, (id) => itemIdByOldId.get(id) ?? null));
    const liveCells = C.layoutItemsOf(existing.layout)
      .map((cell) => C.widgetShape(cell, (id) => id ?? null));
    for (const cell of wantedCells.filter((one) => one.itemKey === null)) {
      console.log(`    ! layout cell ${cell.libraryItemId} names an item not in the export; skipped`);
    }

    const widgets = R.match(
      wantedCells.filter((cell) => cell.itemKey !== null),
      liveCells.filter((cell) => cell.itemKey !== null),
      { keyFor: R.widgetKey, same: R.widgetSame },
    );

    /** Where each wanted cell ended up, so a binding can address it. */
    const layoutIdByOldId = new Map();
    for (const { wanted: cell, existing: there } of [...widgets.unchanged, ...widgets.update]) {
      if (cell.layoutItemId) layoutIdByOldId.set(cell.layoutItemId, there.layoutItemId);
    }

    // Every layout-item id seen so far, whether or not its cell carried an id
    // in the export — this is what makes "the one that wasn't there before"
    // reliable, and the map's values would not be.
    const placed = new Set(liveCells.map((cell) => cell.layoutItemId).filter(Boolean));
    for (const cell of widgets.create) {
      const after = C.layoutItemsOf((await write(ADD_ITEM, {
        d: boardId, i: cell.itemKey, p: { x: cell.x, y: cell.y, w: cell.w, h: cell.h },
      })).addItemToDashboard.layout);
      const minted = after.map((one) => one.id).find((id) => !placed.has(id));
      if (!minted) continue;
      placed.add(minted);
      if (cell.layoutItemId) layoutIdByOldId.set(cell.layoutItemId, minted);
    }
    for (const cell of widgets.extra) {
      if (!prune) {
        console.log(`    - a widget on ${wanted.title} is not in the export; left alone`);
        continue;
      }
      await write(REMOVE_ITEM, { d: boardId, l: cell.layoutItemId });
      done.removed += 1;
    }

    // Geometry. A widget that is already on the board at the wrong place is
    // moved by rewriting the layout rather than by being replaced — the live
    // layout is the base, so every field the product keeps in a cell and this
    // script does not know about survives.
    const moved = widgets.update.filter(({ wanted: cell, existing: there }) =>
      cell.x !== there.x || cell.y !== there.y || cell.w !== there.w || cell.h !== there.h);
    if (moved.length > 0) {
      const byLayoutId = new Map(moved.map(({ wanted: cell, existing: there }) => [there.layoutItemId, cell]));
      const current = (await q(C.DASHBOARDS)).dashboards.find((one) => one.id === boardId);
      const cells = C.layoutItemsOf(current?.layout).map((cell) => {
        const to = byLayoutId.get(cell.id);
        if (!to) return cell;
        const geometry = { ...C.geometryOf(cell), x: to.x, y: to.y, w: to.w, h: to.h };
        return cell.position ? { ...cell, position: geometry } : { ...cell, ...geometry };
      });
      const layout = Array.isArray(current?.layout) ? cells : { ...current.layout, items: cells };
      await write(SET_LAYOUT, { id: boardId, layout });
      console.log(`    ${moved.length} widget(s) on ${wanted.title} moved back to their exported geometry`);
      done.updated += moved.length;
    }

    // The filters. `defaultLatest` has no field on either input type and a
    // hierarchy filter's `levels` are derived from the hierarchy, so neither is
    // replayed; `order` needs the second call because `AddDashboardFilterInput`
    // has no place for it.
    const filters = R.match(
      (wanted.source.filters ?? []).map(C.filterShape),
      (existing.filters ?? []).map(C.filterShape),
      { keyFor: R.filterKey, same: R.filterSame },
    );
    const filterIdByOldId = new Map();
    for (const { wanted: filter, existing: there } of filters.unchanged) {
      filterIdByOldId.set(filter.id, there.id);
    }
    const knownFilterIds = new Set((existing.filters ?? []).map((one) => one.id));
    for (const filter of filters.create) {
      const answered = (await write(ADD_FILTER, {
        d: boardId,
        i: pick(filter, ['dimension', 'hierarchy', 'label', 'type', 'defaultValue', 'hidden']),
      })).addDashboardFilter.filters;
      const minted = answered.find((one) => !knownFilterIds.has(one.id));
      if (!minted) {
        console.log(`    ! filter "${filter.label ?? filter.dimension}" was not created`);
        continue;
      }
      knownFilterIds.add(minted.id);
      filterIdByOldId.set(filter.id, minted.id);
      if (filter.order !== null && filter.order !== undefined) {
        await write(UPDATE_FILTER, { d: boardId, f: minted.id, i: { order: filter.order } });
      }
      if (filter.defaultLatest) {
        console.log(`    ! filter "${filter.label ?? filter.dimension}" had defaultLatest; no input accepts it`);
        done.manual.push(`filter "${filter.label ?? filter.dimension}" on ${wanted.title}: defaultLatest, by hand`);
      }
    }
    for (const { wanted: filter, existing: there } of filters.update) {
      filterIdByOldId.set(filter.id, there.id);
      await write(UPDATE_FILTER, {
        d: boardId,
        f: there.id,
        i: pick(filter, ['label', 'defaultValue', 'hidden', 'order']),
      });
      console.log(`    filter "${filter.label ?? filter.dimension}" updated `
        + `(${differing(filter, there, R.FILTER_FIELDS).join(', ')})`);
      done.updated += 1;
    }
    for (const filter of filters.extra) {
      if (!prune) {
        console.log(`    - filter "${filter.label ?? filter.dimension}" on ${wanted.title} is not in the export; left alone`);
        continue;
      }
      await write(REMOVE_FILTER, { d: boardId, f: filter.id });
      done.removed += 1;
    }

    // The slot-to-filter wiring. `slots` beside it in the export is derived —
    // `addItemToDashboard` regenerates it from the library item's own query — so
    // only `bindings` is replayed. A binding that is already set is left alone,
    // which is what makes a board with wiring on it a no-op on the second run.
    const already = new Set();
    for (const widget of existing.widgetFilterBindings ?? []) {
      for (const binding of widget.bindings ?? []) {
        already.add(R.keyOf(widget.layoutItemId, binding.filterId, binding.parameter, binding.level));
      }
    }
    for (const widget of wanted.source.widgetFilterBindings ?? []) {
      const layoutItemId = layoutIdByOldId.get(widget.layoutItemId);
      for (const binding of widget.bindings ?? []) {
        const filterId = filterIdByOldId.get(binding.filterId);
        if (!layoutItemId || !filterId) {
          console.log(`    ! binding ${binding.parameter ?? binding.filterId} has no counterpart on this board`);
          continue;
        }
        if (already.has(R.keyOf(layoutItemId, filterId, binding.parameter, binding.level))) continue;
        await write(SET_BINDING, {
          d: boardId, l: layoutItemId, f: filterId,
          p: binding.parameter ?? null, lv: binding.level ?? null,
        });
      }
    }

    // What it is subscribed to, by event NAME: the ids are new on a rebuild.
    const wantedSubs = (wanted.source.eventSubscriptions ?? [])
      .map((one) => ({ eventId: eventIdByName.get(one.eventName), daysOfWeek: one.daysOfWeek ?? null }));
    const liveSubs = (existing.eventSubscriptions ?? [])
      .map((one) => ({ eventId: one.eventId, daysOfWeek: one.daysOfWeek ?? null }));
    if (R.signature(wantedSubs) !== R.signature(liveSubs)) {
      await write(UPDATE_DASHBOARD, {
        id: boardId,
        i: { eventSubscriptions: wantedSubs.map((one) => pick(one, ['eventId', 'daysOfWeek'])) },
      }).catch((error) => console.log(`    ! updateDashboard subscriptions: ${error.message.slice(0, 160)}`));
      console.log(`    ${wantedSubs.length} subscription(s) set on ${wanted.title}`);
      done.updated += 1;
    }

    const counted = R.counts(widgets);
    console.log(`  ${wanted.title}: ${counted.create} widget(s) added, ${counted.unchanged + counted.update} already there`
      + `, ${counted.extra} not in the export, ${R.counts(filters).create} filter(s) added`);
  }
})().catch((e) => { console.log('FAILED', e.message); process.exit(1); });
