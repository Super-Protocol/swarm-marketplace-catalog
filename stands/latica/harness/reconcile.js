// Matching an export against what a stand already holds, so a restore
// RECONCILES instead of creating a second copy of everything.
//
// WHY THIS EXISTS. `restore-stand.js` used to create unconditionally. It always
// stopped part-way — at the autopilots, which need an MCP tool that a fresh
// deployment cannot resolve — and re-running it, which is what anybody would
// do, created the library items and the dashboards again. Three attempts on a
// rehearsal stand turned 15 library items into 37 and 2 dashboards into 4
// (SUP-917, 2026-11-25). The failure mode was not "some of it is missing", it
// was "the library is silently duplicated".
//
// THE PROBLEM WITH MATCHING BY NAME ALONE: the stand's own export has two pairs
// of library items that share a title — two *Endoscopist Scorecard & Outlier
// Analysis* and two *Procedure Volume Trajectory (Monthly)* — with different
// queries behind them. So a title is a bucket, not a key, and a reconcile that
// assumed otherwise would drop half of each pair.
//
// WHAT {@link match} DOES, therefore, is reconcile a MULTISET per name:
//
//   1. bucket both sides by key — the stable name a person chose;
//   2. inside a bucket, pair every wanted thing with an existing one that is
//      already IDENTICAL, so a second run of the restore writes nothing;
//   3. pair what is left in order, and update those in place;
//   4. anything still wanted is created, and anything still on the stand that
//      the export does not account for is reported as an extra.
//
// Nothing here performs I/O or touches a credential: it is given two plain
// lists and answers with a plan. That is what makes it testable — see
// `tests/reconcile.test.js`, which pins the property the rehearsal disproved:
// applying a plan and planning again yields nothing to do.
'use strict';

/** Stable JSON: object keys sorted at every depth, so two definitions that
 *  differ only in the order their fields came back in compare equal. */
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  }
  return value;
}

/** A comparable string for any value. `undefined` and `null` are the same
 *  thing here: a field absent from an export and a field the API answered
 *  `null` for describe the same configuration. */
const signature = (value) => JSON.stringify(canonical(value ?? null) ?? null);

/** Compare two things on a named field set and nothing else. Everything the
 *  API derives — ids, timestamps, versions, run state — is outside the set by
 *  construction, which is why "identical" can be decided at all. */
const sameOn = (fields) => (left, right) =>
  fields.every((field) => signature(left[field]) === signature(right[field]));

/** Join parts into a key that cannot be forged by a name containing the
 *  separator. */
const keyOf = (...parts) => parts.map((part) => String(part ?? '')).join('\u0000');

/**
 * Reconcile `wanted` against `existing`.
 *
 * @param wanted   what the export says should be there
 * @param existing what the stand has now
 * @param keyFor   the stable name — a bucket, not necessarily unique
 * @param same     whether two things in one bucket are already identical
 * @returns `{ unchanged, update, create, extra }`. `unchanged` and `update`
 *          carry `{ wanted, existing }` pairs; `create` carries wanted things;
 *          `extra` carries existing things the export does not account for.
 *
 * Deterministic: buckets are walked in the order `wanted` introduced them, then
 * in the order `existing` introduced the rest, so the same two lists always
 * produce the same plan and a log of one run can be compared with another.
 */
function match(wanted, existing, { keyFor, same }) {
  const buckets = new Map();
  const bucket = (key) => {
    if (!buckets.has(key)) buckets.set(key, { wanted: [], existing: [] });
    return buckets.get(key);
  };
  for (const thing of wanted) bucket(keyFor(thing)).wanted.push(thing);
  for (const thing of existing) bucket(keyFor(thing)).existing.push(thing);

  const plan = { unchanged: [], update: [], create: [], extra: [] };
  for (const group of buckets.values()) {
    const free = [...group.existing];
    const pending = [];
    // Identical first, across the whole bucket: pairing in order would call an
    // item "changed" merely because its twin came back in another order, and
    // then write to both.
    for (const one of group.wanted) {
      const at = free.findIndex((candidate) => same(one, candidate));
      if (at === -1) pending.push(one);
      else plan.unchanged.push({ wanted: one, existing: free.splice(at, 1)[0] });
    }
    for (const one of pending) {
      if (free.length > 0) plan.update.push({ wanted: one, existing: free.shift() });
      else plan.create.push(one);
    }
    plan.extra.push(...free);
  }
  return plan;
}

/** `created/updated/unchanged/extra`, for a log line and for a test to assert
 *  on. */
const counts = (plan) => ({
  unchanged: plan.unchanged.length,
  update: plan.update.length,
  create: plan.create.length,
  extra: plan.extra.length,
});

/** A plan that writes nothing. What a second restore must produce. */
const isNoOp = (plan) => plan.update.length === 0 && plan.create.length === 0;

// ---------------------------------------------------------------------------
// The stand's four kinds of configuration. Each one is a key and a field set,
// and nothing else — the restore reads them from here so that what "the same
// item" means is written down once.
// ---------------------------------------------------------------------------

/** A library item's bucket: its type and its title. Not its id, which is new on
 *  every rebuild, and not its content, which is what an update is FOR. */
const libraryKey = (item) => keyOf(item.type, item.title);

/** What makes two library items the same item. `folderId` is compared through
 *  the caller's mapping, not here, because an id means nothing across stands;
 *  `tags` is set by its own mutation and is compared all the same. */
const LIBRARY_FIELDS = ['title', 'description', 'type', 'visibility', 'visualizationSpec', 'kind',
  'query', 'parameterValues', 'customSql', 'customBindings', 'datasourceId', 'aiContext', 'modelId',
  'tags', 'folderPath'];
const librarySame = sameOn(LIBRARY_FIELDS);

/** A folder's bucket is its PATH, not its name: two folders called "Quality"
 *  under different parents are two folders. */
const folderKey = (folder) => keyOf(folder.folderPath);
const folderSame = sameOn(['folderPath']);

const dashboardKey = (dashboard) => keyOf(dashboard.title);
/** Only the board's own settings. Its widgets and filters are reconciled
 *  separately, because each is a list with its own mutations. */
const DASHBOARD_FIELDS = ['title', 'description', 'about', 'visibility', 'tags',
  'refreshTtlSeconds', 'autoRefreshOnData', 'periodAnchor'];
const dashboardSame = sameOn(DASHBOARD_FIELDS);

/** A widget's bucket is WHICH ITEM it shows — `itemKey`, the reference
 *  `widgetShape`'s `resolve` produced, which is the item's id on the TARGET on
 *  both sides. Geometry is not in the key: a widget that moved is the same
 *  widget at another place, and the layout is patched rather than the widget
 *  replaced. */
const widgetKey = (widget) => keyOf(widget.itemKey);
const widgetSame = sameOn(['itemKey', 'x', 'y', 'w', 'h']);

/** A filter's bucket is what it filters on, because that is the part
 *  `updateDashboardFilter` cannot change: a filter whose dimension, hierarchy
 *  or type differs has to be removed and added, and one whose label, default or
 *  visibility differs can be updated in place. */
const filterKey = (filter) => keyOf(filter.dimension, filter.hierarchy, filter.type);
const FILTER_FIELDS = ['dimension', 'hierarchy', 'type', 'label', 'defaultValue', 'hidden', 'order'];
const filterSame = sameOn(FILTER_FIELDS);

const autopilotKey = (autopilot) => keyOf(autopilot.name);
const AUTOPILOT_FIELDS = ['name', 'promptTemplate', 'modelId', 'enabled', 'cronExpression',
  'visibility', 'signalMode', 'signalBlock', 'allowedMcpTools', 'parameters', 'subscriptions'];
const autopilotSame = sameOn(AUTOPILOT_FIELDS);

/** An MCP server's bucket is its slug, which is also what its tools are bound
 *  under: `mcp_<slug>_<tool>` is the name an autopilot's allowlist carries, so a
 *  server restored under another slug silently breaks every autopilot that
 *  names one of its tools. */
const mcpKey = (server) => keyOf(server.slug);
/** The auth header VALUE is deliberately not here, and is not in the export
 *  either: it is the clinic's own bearer token. `authHeaderNames` is, so a
 *  server that lost its credential is visible as a difference. */
const MCP_FIELDS = ['name', 'slug', 'url', 'description', 'enabled', 'authHeaderNames'];
const mcpSame = sameOn(MCP_FIELDS);

module.exports = {
  canonical,
  signature,
  sameOn,
  keyOf,
  match,
  counts,
  isNoOp,
  libraryKey,
  librarySame,
  LIBRARY_FIELDS,
  folderKey,
  folderSame,
  dashboardKey,
  dashboardSame,
  DASHBOARD_FIELDS,
  widgetKey,
  widgetSame,
  filterKey,
  filterSame,
  FILTER_FIELDS,
  autopilotKey,
  autopilotSame,
  AUTOPILOT_FIELDS,
  mcpKey,
  mcpSame,
  MCP_FIELDS,
};
