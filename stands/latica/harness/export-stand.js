// Export what a person configured on the stand by hand, so a rebuild re-applies
// it instead of re-deriving it.
//
// The semantic model, the dashboards, the library items they are built from, the
// two autopilots and the clinic's MCP server registration live in the
// DEPLOYMENT's PostgreSQL. None of them is in a manifest, in the listing or in
// any backup the platform takes: a published cluster space is frozen, so there
// is no exec and no pg_dump, and a re-create starts with an empty database.
// This script is the only copy there is.
//
// Every call here is a GraphQL **query**. Nothing mutates, and nothing fetches
// a credential: `adminDataSourceCredentials` is deliberately not called, and
// the fields that carry a token or a signed URL (`lastDatasetToken`,
// `thumbnailUrl`) are dropped on the way out by `DROP` in `stand-config.js` — a
// dataset token is a bearer for the rows behind it.
//
// The documents are in `stand-config.js`, which `restore-stand.js` reads the
// target stand through as well. That is not tidiness: the restore decides
// whether a thing it is about to write is already there, and it can only decide
// that if both sides were read through the same document.
//
// WHERE THE MCP SERVER'S CONFIGURATION LIVES, since it was the one piece a
// rebuild lost outright (SUP-917). The registration is tenant configuration in
// the deployment's own database — Administration → MCP servers, which is what
// `harness/mcpreg.js` drives — so `mcp-servers.json` captures it in full: name,
// slug, address, the egress wording it carries as its description, whether it is
// switched on, and the tools it resolved.
//
// The one piece NOT here is the `Authorization` header's value: the bearer token
// Cypress Bay issued. Only its header NAME is exported — the API has no read for
// the value, and this script would not call one if it did. The token lives in
// exactly two places, neither of them this repository: the operator's
// `clinic.token` file beside the harness, and the clinic's own
// `/etc/clinic-scheduling/grants.json` on the marketplace host, which is where a
// replacement is read from. `restore-stand.js` takes it by path, the same way
// `mcpreg.js` always has.
//
//   APP_BASE=https://analyst-stand.conf-apps.superprotocol.dev \
//   APP_USER=<installation admin> PASS_FILE=./stand.administratorPassword \
//   OUT=../configuration-backup node harness/export-stand.js
const fs = require('fs');
const path = require('path');
const { signIn, graphql } = require('./applogin');
const {
  clean, DASHBOARDS, LIBRARY, AUTOPILOTS, MCP, TENANTS, SEMANTIC,
} = require('./stand-config');

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
  // free; `restore-stand.js` retries only its reads, for the same reason.
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

  // Dashboards, library, autopilots and tool servers are scoped to the
  // signed-in account's own tenant and visibility — there is no cross-tenant
  // admin read for them — so what lands here is what APP_USER can see, and the
  // manifest says so.
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

  // The tool servers, after the autopilots that depend on them: an autopilot's
  // `allowedMcpTools` names a tool by its bound name, `mcp_<slug>_<tool>`, and
  // the only thing that can say which server that is, is this file. An
  // installation with MCP switched off answers an error rather than an empty
  // list, and that is not a reason to lose the rest of the export.
  let mcpServers = [];
  try {
    mcpServers = (await q(MCP)).mcpServers;
    write('mcp-servers.json', mcpServers);
    const resolvable = new Set(mcpServers.flatMap((one) => (one.tools ?? []).map((t) => t.boundName)));
    for (const tool of new Set(autopilots.flatMap((one) => one.allowedMcpTools ?? []))) {
      if (!resolvable.has(tool)) {
        console.log(`  ! an autopilot allows ${tool}, which no exported server resolves`);
      }
    }
    for (const server of mcpServers.filter((one) => one.hasAuth)) {
      console.log(`  ${server.slug}: auth header ${server.authHeaderNames.join(', ')} — the VALUE is not`
        + ' exported; the restore reads the token from MCP_TOKEN_FILE');
    }
  } catch (error) {
    console.log(`mcp servers not exported: ${error.message.slice(0, 200)}`);
  }

  write('MANIFEST.json', {
    exportedAt: new Date().toISOString(),
    stand: base,
    scopedTo: { user: process.env.APP_USER, note: 'dashboards, library, autopilots and tool servers are this account\'s tenant and visibility' },
    tenants,
    counts: {
      dashboards: dashboards.length,
      libraryItems: library.libraryItems.length,
      libraryFolders: library.libraryFolders.length,
      autopilots: autopilots.length,
      // Tenant rows only. A global row is rendered from the listing's
      // parameters and is nothing a restore can put back, so counting it here
      // would make a clean restore look short by one.
      mcpServers: mcpServers.filter((one) => !one.global).length,
    },
  });
})().catch((e) => { console.log('FAILED', e.message); process.exit(1); });
