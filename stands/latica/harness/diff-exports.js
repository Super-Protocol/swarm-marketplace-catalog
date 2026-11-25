// Two exports, compared. The verdict the rehearsal turns on.
//
//   BEFORE=../configuration-backup AFTER=../rehearsal-backup node harness/diff-exports.js
//
// SUP-939's acceptance criterion is "zero differences in counts and names"
// between the stand's export and the export of a rebuilt stand it was restored
// into. The first time that was checked (SUP-917, 2026-11-25) it was checked by
// reading two manifests, which is how "15 → 37" was found but also how a
// changed DEFINITION under an unchanged name would have been missed: the counts
// would have matched and the diff would have read clean.
//
// So this compares the same way `restore-stand.js` reconciles — same keys, same
// field sets, out of `reconcile.js` and `stand-config.js` — and says, per kind:
//
//   missing  in BEFORE and not in AFTER          (the restore lost it)
//   extra    in AFTER and not in BEFORE          (the restore duplicated it, or
//                                                 somebody added it)
//   changed  the same name, a different definition
//
// A clean run prints `no differences` and exits 0. Anything else exits 1 and
// names every difference, because a rehearsal whose verdict has to be eyeballed
// is a rehearsal whose verdict is an opinion.
//
// It is a READ of two directories on disk. It does not sign in anywhere and it
// changes nothing.
const fs = require('fs');
const path = require('path');
const C = require('./stand-config');
const R = require('./reconcile');

const read = (dir, name) => JSON.parse(fs.readFileSync(path.join(dir, name), 'utf8'));
const maybe = (dir, name, fallback) =>
  (fs.existsSync(path.join(dir, name)) ? read(dir, name) : fallback);

/** One export, in the shapes the comparison uses. */
function load(dir) {
  const manifest = read(dir, 'MANIFEST.json');
  const folders = read(dir, 'library-folders.json');
  const paths = C.folderPaths(folders);
  return {
    dir,
    manifest,
    models: new Map((manifest.tenants ?? []).map((tenant) => [tenant.name, read(dir, tenant.file)])),
    kinds: {
      'library folders': {
        things: folders.map((one) => C.folderShape(one, paths)),
        keyFor: R.folderKey,
        same: R.folderSame,
        label: (one) => one.folderPath,
      },
      'library items': {
        things: read(dir, 'library-items.json').map((one) => C.libraryShape(one, paths)),
        keyFor: R.libraryKey,
        same: R.librarySame,
        label: (one) => `${one.title} (${one.type})`,
        fields: R.LIBRARY_FIELDS,
      },
      dashboards: {
        things: read(dir, 'dashboards.json').map(C.dashboardShape),
        keyFor: R.dashboardKey,
        same: R.dashboardSame,
        label: (one) => one.title,
        fields: R.DASHBOARD_FIELDS,
      },
      autopilots: {
        things: maybe(dir, 'autopilots.json', []).map(C.autopilotShape),
        keyFor: R.autopilotKey,
        same: R.autopilotSame,
        label: (one) => one.name,
        fields: R.AUTOPILOT_FIELDS,
      },
      'tool servers': {
        // A global row is rendered from the listing's parameters, so it is not
        // part of what a restore is answerable for.
        things: maybe(dir, 'mcp-servers.json', []).filter((one) => !one.global).map(C.mcpShape),
        keyFor: R.mcpKey,
        same: R.mcpSame,
        label: (one) => one.slug,
        fields: R.MCP_FIELDS,
      },
    },
  };
}

/** A dashboard's widgets, named by the TITLE of the item each one shows: the
 *  item ids differ between two stands by construction, so this is the only
 *  thing two exports can be compared on. A pair of same-titled items makes one
 *  widget indistinguishable from the other — which is why the item's own
 *  definition is compared separately, above. */
function widgetsOf(board, items) {
  const titleById = new Map(items.map((one) => [one.id, `${one.title} (${one.type})`]));
  return C.layoutItemsOf(board.layout).map((cell) => {
    const geometry = C.geometryOf(cell);
    return `${titleById.get(cell.libraryItemId ?? cell.itemId) ?? '?'} @ `
      + `${geometry.x ?? 0},${geometry.y ?? 0} ${geometry.w ?? 6}x${geometry.h ?? 6}`;
  }).sort();
}

(async () => {
  const before = load(path.resolve(process.env.BEFORE || 'configuration-backup'));
  const after = load(path.resolve(process.env.AFTER || 'rehearsal-backup'));

  console.log(`BEFORE  ${before.manifest.stand}  ${before.manifest.exportedAt}`);
  console.log(`AFTER   ${after.manifest.stand}  ${after.manifest.exportedAt}\n`);

  const differences = [];
  const say = (text) => { differences.push(text); console.log(`  ! ${text}`); };

  // The semantic model, per tenant and by name: a rebuilt stand has new tenant
  // ids, and the model document is keyed by code throughout, so the two are
  // comparable whole.
  console.log('semantic model');
  for (const [name, document] of before.models) {
    const other = after.models.get(name);
    const counts = (one) => `${(one.datasets ?? []).length} datasets, ${(one.dimensions ?? []).length} dimensions, `
      + `${(one.metrics ?? []).length} metrics, ${(one.hierarchies ?? []).length} hierarchies`;
    if (!other) {
      say(`tenant "${name}" is in BEFORE and not in AFTER (${counts(document)})`);
      continue;
    }
    if (R.signature(document) !== R.signature(other)) {
      say(`the model of "${name}" differs: BEFORE ${counts(document)}; AFTER ${counts(other)}`);
      for (const part of ['datasets', 'dimensions', 'metrics', 'hierarchies', 'bindings']) {
        const mine = new Set((document[part] ?? []).map((one) => one.code ?? R.signature(one)));
        const theirs = new Set((other[part] ?? []).map((one) => one.code ?? R.signature(one)));
        for (const code of mine) if (!theirs.has(code)) say(`  ${name}: ${part} "${code}" missing`);
        for (const code of theirs) if (!mine.has(code)) say(`  ${name}: ${part} "${code}" extra`);
      }
      continue;
    }
    console.log(`  ${name}: identical (${counts(document)})`);
  }
  for (const name of after.models.keys()) {
    if (!before.models.has(name)) {
      const extra = after.models.get(name);
      // A tenant with no model is not a difference the restore is answerable
      // for: a rebuilt stand may carry one the export never had a model for.
      if ((extra.datasets ?? []).length > 0) say(`tenant "${name}" is in AFTER and not in BEFORE`);
      else console.log(`  ${name}: in AFTER only, and empty — not a difference`);
    }
  }

  for (const [kind, spec] of Object.entries(before.kinds)) {
    const other = after.kinds[kind];
    const plan = R.match(spec.things, other.things, { keyFor: spec.keyFor, same: spec.same });
    const counted = R.counts(plan);
    console.log(`\n${kind}: BEFORE ${spec.things.length}, AFTER ${other.things.length}`
      + ` — ${counted.unchanged} identical, ${counted.update} changed, ${counted.create} missing`
      + `, ${counted.extra} extra`);
    for (const one of plan.create) say(`${kind}: "${spec.label(one)}" is missing from AFTER`);
    for (const one of plan.extra) say(`${kind}: "${other.label(one)}" is in AFTER and not in BEFORE`);
    for (const { wanted, existing } of plan.update) {
      const fields = (spec.fields ?? []).filter(
        (field) => R.signature(wanted[field]) !== R.signature(existing[field]),
      );
      say(`${kind}: "${spec.label(wanted)}" differs in ${fields.join(', ') || 'a field outside the compared set'}`);
    }
  }

  // A dashboard's contents, which `dashboardSame` deliberately leaves out: it
  // compares a board's own settings, and the widgets are a list of their own.
  console.log('\ndashboard contents');
  const beforeItems = read(before.dir, 'library-items.json');
  const afterItems = read(after.dir, 'library-items.json');
  for (const board of read(before.dir, 'dashboards.json')) {
    const other = read(after.dir, 'dashboards.json').find((one) => one.title === board.title);
    if (!other) continue; // already reported as missing above
    const mine = widgetsOf(board, beforeItems);
    const theirs = widgetsOf(other, afterItems);
    if (R.signature(mine) !== R.signature(theirs)) {
      say(`${board.title}: ${mine.length} widget(s) in BEFORE, ${theirs.length} in AFTER`);
      for (const one of mine) if (!theirs.includes(one)) say(`  ${board.title}: widget missing — ${one}`);
      for (const one of theirs) if (!mine.includes(one)) say(`  ${board.title}: widget extra — ${one}`);
    } else {
      console.log(`  ${board.title}: ${mine.length} widget(s), identical`);
    }
    const filters = (one) => (one.filters ?? []).map(C.filterShape);
    const filterPlan = R.match(filters(board), filters(other), { keyFor: R.filterKey, same: R.filterSame });
    if (!R.isNoOp(filterPlan) || filterPlan.extra.length > 0) {
      say(`${board.title}: filters differ — ${JSON.stringify(R.counts(filterPlan))}`);
    }
    const subs = (one) => (one.eventSubscriptions ?? [])
      .map((sub) => `${sub.eventName}:${JSON.stringify(sub.daysOfWeek ?? null)}`).sort();
    if (R.signature(subs(board)) !== R.signature(subs(other))) {
      say(`${board.title}: event subscriptions differ — BEFORE ${JSON.stringify(subs(board))}, `
        + `AFTER ${JSON.stringify(subs(other))}`);
    }
  }

  console.log('');
  if (differences.length === 0) {
    console.log('no differences — every count and every name matches, and so does every definition.');
    return;
  }
  console.log(`${differences.length} difference(s). This is not a clean restore.`);
  process.exit(1);
})().catch((e) => { console.log('FAILED', e.message); process.exit(1); });
