// What "the stand's configuration" IS: the GraphQL reads that capture it, and
// the stand-independent shape each piece is compared in.
//
// It lives in one file because `export-stand.js` and `restore-stand.js` have to
// agree. The restore reconciles an export against what a target stand holds
// now, and it can only decide that two things are identical if both sides were
// read through the same document and reduced to the same fields. When the two
// scripts each carried their own copy, the first field one of them forgot would
// have become a permanent "changed" — a write on every run, for ever, with
// nothing to show for it.
//
// Every document here is a QUERY. Nothing in this file mutates, and nothing in
// it reads a credential: see `DROP` below and the note on `authHeaderNames`.
'use strict';

/** Fields that are a bearer for data, or that move on their own. Never
 *  exported, never compared. */
const DROP = new Set(['lastDatasetToken', 'thumbnailUrl', 'thumbnailAt', 'assistantUnread']);

/** Strip the fields above wherever they appear, at any depth. */
function clean(value) {
  if (Array.isArray(value)) return value.map(clean);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).filter(([key]) => !DROP.has(key)).map(([key, inner]) => [key, clean(inner)]),
    );
  }
  return value;
}

/**
 * A dashboard whole. Two halves of `widgetFilterBindings` matter differently on
 * the way back: `slots` is DERIVED — `addItemToDashboard` regenerates it from
 * the library item's own query — while `bindings` is the slot-to-filter wiring
 * somebody chose, and is the only part the restore has to replay.
 */
const DASHBOARDS = `{
  dashboards {
    id title description about visibility layout refreshTtlSeconds autoRefreshOnData
    periodAnchor tags ownerId createdAt updatedAt
    filters { id dimension hierarchy label type defaultValue defaultLatest hidden order
      dimensionId dimensionDisplayName dimensionKind timeRole hierarchyId hierarchyDisplayName
      levels { dimensionId code displayName } }
    widgetFilterBindings { layoutItemId appliedFilterValues
      slots { name dimension type label dimensionId dimensionDisplayName hierarchy levels }
      bindings { filterId state parameter via level } }
    eventSubscriptions { eventId eventName daysOfWeek }
    items { id title }
  }
}`;

const LIBRARY = `{
  libraryItems {
    id title description type visibility ownerId visualizationSpec kind query
    parameterValues customSql customBindings datasourceId aiContext modelId
    version sourceThreadId sourceMessageId tags folderId createdAt updatedAt
    runDatasetCode runDataAsOf
  }
  libraryFolders { id name parentId position itemCount }
}`;

const AUTOPILOTS = `{
  autopilots {
    id name promptTemplate modelId enabled cronExpression visibility ownerId
    signalMode signalBlock allowedMcpTools parameters createdAt updatedAt
    eventSubscriptions { eventId eventName daysOfWeek
      signalFilter { autopilotIds minPriority codes scope { key value } } }
  }
}`;

/**
 * The tenant's tool servers. `authHeaderNames` rather than the headers: the API
 * offers no read of a header's value at all, which is the right design, and
 * nothing here would export it if it did.
 *
 * `global` separates the two mechanisms. A global row is rendered from the
 * listing's `mcpServerName` / `mcpServerUrl` parameters into the attested
 * ConfigMap and has no create mutation behind it, so it is recorded for the
 * diff and skipped by the restore.
 */
const MCP = `{
  mcpServers {
    id name slug url description enabled global enabledForTenant
    hasAuth authHeaderNames toolCount lastError lastCheckedAt
    tools { name boundName readOnly }
  }
}`;

const TENANTS = '{ adminTenants { id name } }';
const SEMANTIC = 'query ($t: String) { adminSemanticModelExport(tenantId: $t, includeDrafts: true) }';

/** Every data source the signed-in administrator can see, which is how the
 *  restore checks that the cards a rebuild needs are actually mounted before it
 *  writes anything. */
const DATA_SOURCES = `{
  adminDataSources(pagination: { limit: 100, offset: 0 }) {
    items { id tenantId datasourceId name dataSourceProviderId schemaTableCount }
  }
}`;

/** A dashboard's layout is `{ items: [...], version }`; very early ones were the
 *  array itself. Both are accepted because a backup outlives its writer. */
const layoutItemsOf = (layout) => (Array.isArray(layout) ? layout : (layout?.items ?? []));

/** A cell's geometry: `position: { x, y, w, h }` today, the cell itself in older
 *  exports. */
const geometryOf = (cell) => cell.position ?? cell;

/**
 * `folderId` → the folder's PATH, built from the folder list the same read
 * returned. A path is the only stand-independent name a folder has: ids are new
 * on every rebuild, and a bare name is ambiguous under two parents.
 */
function folderPaths(folders) {
  const byId = new Map((folders ?? []).map((folder) => [folder.id, folder]));
  const paths = new Map();
  const walk = (id, seen) => {
    if (id === null || id === undefined) return null;
    if (paths.has(id)) return paths.get(id);
    const folder = byId.get(id);
    // A parent the read did not return, or a cycle a corrupt export could
    // carry: name the folder by itself rather than recurse for ever.
    if (!folder || seen.has(id)) return folder ? folder.name : null;
    seen.add(id);
    const parent = walk(folder.parentId, seen);
    const path = parent ? `${parent}/${folder.name}` : folder.name;
    paths.set(id, path);
    return path;
  };
  for (const folder of folders ?? []) walk(folder.id, new Set());
  return paths;
}

/** A folder as the reconcile compares it. */
const folderShape = (folder, paths) => ({
  folderPath: paths.get(folder.id) ?? folder.name,
  name: folder.name,
  source: folder,
});

/** A library item as the reconcile compares it: the definition somebody wrote,
 *  with the folder named by path rather than by id, and with tags in a stable
 *  order so "the same tags, listed differently" is not a change. */
const libraryShape = (item, paths) => ({
  title: item.title,
  description: item.description,
  type: item.type,
  visibility: item.visibility,
  visualizationSpec: item.visualizationSpec,
  kind: item.kind,
  query: item.query,
  parameterValues: item.parameterValues,
  customSql: item.customSql,
  customBindings: item.customBindings,
  datasourceId: item.datasourceId,
  aiContext: item.aiContext,
  modelId: item.modelId,
  tags: [...(item.tags ?? [])].sort(),
  folderPath: item.folderId ? (paths.get(item.folderId) ?? null) : null,
  source: item,
});

/** A dashboard's own settings. Its widgets and filters are reconciled
 *  separately, each with its own mutations. */
const dashboardShape = (dashboard) => ({
  title: dashboard.title,
  description: dashboard.description,
  about: dashboard.about,
  visibility: dashboard.visibility,
  tags: [...(dashboard.tags ?? [])].sort(),
  refreshTtlSeconds: dashboard.refreshTtlSeconds,
  autoRefreshOnData: dashboard.autoRefreshOnData,
  periodAnchor: dashboard.periodAnchor,
  source: dashboard,
});

/**
 * One widget, named by the library item it shows rather than by the layout-item
 * id, which `addItemToDashboard` mints fresh every time.
 *
 * `resolve` turns the cell's own library-item id into a reference that means the
 * same thing on both stands: for an exported cell it is the id the restore gave
 * that item on the target, for a live cell it is the id itself. Going through
 * the item's TITLE instead would have been wrong here — the stand's library has
 * two items called *Endoscopist Scorecard & Outlier Analysis* with different
 * queries behind them, and a widget has to come back pointing at the one it
 * pointed at.
 */
const widgetShape = (cell, resolve) => {
  const geometry = geometryOf(cell);
  return {
    itemKey: resolve(cell.libraryItemId ?? cell.itemId) ?? null,
    libraryItemId: cell.libraryItemId ?? cell.itemId ?? null,
    layoutItemId: cell.id ?? null,
    x: geometry.x ?? 0,
    y: geometry.y ?? 0,
    w: geometry.w ?? 6,
    h: geometry.h ?? 6,
    source: cell,
  };
};

const filterShape = (filter) => ({
  dimension: filter.dimension,
  hierarchy: filter.hierarchy,
  type: filter.type,
  label: filter.label,
  defaultValue: filter.defaultValue,
  hidden: filter.hidden ?? false,
  order: filter.order,
  defaultLatest: filter.defaultLatest ?? false,
  id: filter.id,
  source: filter,
});

/** An autopilot, with its subscriptions folded into the comparison: a
 *  subscription is named by the event's own `eventName`, because the event id is
 *  new on every rebuild. */
const autopilotShape = (autopilot) => ({
  name: autopilot.name,
  promptTemplate: autopilot.promptTemplate,
  modelId: autopilot.modelId,
  enabled: autopilot.enabled,
  cronExpression: autopilot.cronExpression,
  visibility: autopilot.visibility,
  signalMode: autopilot.signalMode ?? false,
  signalBlock: autopilot.signalBlock,
  allowedMcpTools: [...(autopilot.allowedMcpTools ?? [])].sort(),
  parameters: autopilot.parameters,
  subscriptions: (autopilot.eventSubscriptions ?? [])
    .map((one) => ({
      eventName: one.eventName,
      daysOfWeek: one.daysOfWeek ?? null,
      signalFilter: one.signalFilter ?? null,
    }))
    .sort((left, right) => String(left.eventName).localeCompare(String(right.eventName))),
  source: autopilot,
});

/** A tool server. The header VALUE is neither exported nor compared; the header
 *  NAMES are, so a server that lost its credential shows up as a difference. */
const mcpShape = (server) => ({
  name: server.name,
  slug: server.slug,
  url: server.url,
  description: server.description,
  enabled: server.enabled,
  authHeaderNames: [...(server.authHeaderNames ?? [])].sort(),
  global: server.global ?? false,
  hasAuth: server.hasAuth ?? false,
  tools: server.tools ?? [],
  source: server,
});

module.exports = {
  DROP,
  clean,
  DASHBOARDS,
  LIBRARY,
  AUTOPILOTS,
  MCP,
  TENANTS,
  SEMANTIC,
  DATA_SOURCES,
  layoutItemsOf,
  geometryOf,
  folderPaths,
  folderShape,
  libraryShape,
  dashboardShape,
  widgetShape,
  filterShape,
  autopilotShape,
  mcpShape,
};
