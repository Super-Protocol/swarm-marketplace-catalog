// Export what a person configured on the stand by hand, so a rebuild re-applies
// it instead of re-deriving it.
//
// The semantic model, the dashboards, the library items they are built from and
// the two autopilots live in the DEPLOYMENT's PostgreSQL. None of them is in a
// manifest, in the listing or in any backup the platform takes: a published
// cluster space is frozen, so there is no exec and no pg_dump, and a re-create
// starts with an empty database. This script is the only copy there is.
//
// Every call here is a GraphQL **query**. Nothing mutates, and nothing fetches
// a credential: `adminDataSourceCredentials` is deliberately not called, and
// the fields that carry a token or a signed URL (`lastDatasetToken`,
// `thumbnailUrl`) are dropped on the way out — a dataset token is a bearer for
// the rows behind it.
//
//   APP_BASE=https://analyst-stand.conf-apps.superprotocol.dev \
//   APP_USER=<installation admin> PASS_FILE=./stand.administratorPassword \
//   OUT=../configuration-backup node harness/export-stand.js
const fs = require('fs');
const path = require('path');
const { signIn, graphql } = require('./applogin');

/** Fields that are a bearer for data, or that move on their own. Not exported. */
const DROP = new Set(['lastDatasetToken', 'thumbnailUrl', 'thumbnailAt', 'assistantUnread']);

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

const TENANTS = '{ adminTenants { id name } }';
const SEMANTIC = 'query ($t: String) { adminSemanticModelExport(tenantId: $t, includeDrafts: true) }';

/** Strip the fields above wherever they appear, at any depth. */
function clean(value) {
  if (Array.isArray(value)) return value.map(clean);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).filter(([k]) => !DROP.has(k)).map(([k, v]) => [k, clean(v)]),
    );
  }
  return value;
}

const slug = (name) => name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

(async () => {
  const base = process.env.APP_BASE;
  const out = path.resolve(process.env.OUT || 'configuration-backup');
  fs.mkdirSync(out, { recursive: true });
  const cookie = await signIn({
    base,
    username: process.env.APP_USER,
    password: fs.readFileSync(process.env.PASS_FILE, 'utf8').trim(),
  });
  // The stand's gateway answers 502 now and then (the same flake `ask.js`
  // retries its sign-in for). Every call here is a read, so retrying one is
  // free; `restore-stand.js` deliberately does NOT retry, because its calls
  // create things.
  const q = async (query, variables) => {
    for (let attempt = 1; ; attempt += 1) {
      try {
        return await graphql({ base, cookie, query, variables });
      } catch (error) {
        if (attempt === 3) throw error;
        console.log(`  retrying after: ${error.message.slice(0, 120).replace(/\s+/g, ' ')}`);
        await new Promise((resolve) => { setTimeout(resolve, 4000 * attempt); });
      }
    }
  };

  const write = (name, body) => {
    const file = path.join(out, name);
    fs.writeFileSync(file, `${JSON.stringify(clean(body), null, 1)}\n`, 'utf8');
    console.log(`${name}  ${fs.statSync(file).size} bytes`);
    return body;
  };

  // The semantic model, per tenant. `adminSemanticModelExport` is the product's
  // own export — the same document `adminSemanticModelImport` reads back — so
  // the restore is an import rather than a re-derivation.
  const { adminTenants } = await q(TENANTS);
  const tenants = [];
  for (const tenant of adminTenants) {
    const model = (await q(SEMANTIC, { t: tenant.id })).adminSemanticModelExport;
    const counts = {
      datasets: model.datasets?.length ?? 0,
      dimensions: model.dimensions?.length ?? 0,
      metrics: model.metrics?.length ?? 0,
      hierarchies: model.hierarchies?.length ?? 0,
    };
    write(`semantic-model.${slug(tenant.name)}.json`, model);
    tenants.push({ ...tenant, file: `semantic-model.${slug(tenant.name)}.json`, counts });
  }

  // Dashboards, library and autopilots are scoped to the signed-in account's
  // own tenant and visibility — there is no cross-tenant admin read for them —
  // so what lands here is what APP_USER can see, and the manifest says so.
  const dashboards = (await q(DASHBOARDS)).dashboards;
  write('dashboards.json', dashboards);
  const library = await q(LIBRARY);
  write('library-items.json', library.libraryItems);
  write('library-folders.json', library.libraryFolders);
  let autopilots = [];
  try {
    autopilots = (await q(AUTOPILOTS)).autopilots;
    write('autopilots.json', autopilots);
  } catch (error) {
    console.log(`autopilots not exported: ${error.message.slice(0, 200)}`);
  }

  write('MANIFEST.json', {
    exportedAt: new Date().toISOString(),
    stand: base,
    scopedTo: { user: process.env.APP_USER, note: 'dashboards, library and autopilots are this account\'s tenant and visibility' },
    tenants,
    counts: {
      dashboards: dashboards.length,
      libraryItems: library.libraryItems.length,
      libraryFolders: library.libraryFolders.length,
      autopilots: autopilots.length,
    },
  });
})().catch((e) => { console.log('FAILED', e.message); process.exit(1); });
