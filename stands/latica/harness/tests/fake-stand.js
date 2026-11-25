// A stand with no cluster behind it: the oauth2-proxy sign-in dance and the
// slice of the agent's GraphQL API that `restore-stand.js` uses, over an
// in-memory store.
//
// WHY. The only other way to find out whether the restore is idempotent is to
// rebuild a deployment and run it twice, which costs a cluster space and an
// afternoon — it is how SUP-917 found out that it was not. This answers the same
// question in 200 ms, every time anybody touches the script, and it answers it
// about the SCRIPT: the real `restore-stand.js` process, its real mutations, in
// their real order. `reconcile.test.js` covers the matching underneath it; this
// covers the wiring on top.
//
// WHAT IT IS NOT. It is not the product. It enforces almost nothing — no
// authorisation, no validation, no quota — and it answers in the shapes the
// restore reads, which is what makes it a test double rather than a second
// implementation to keep true. Two behaviours ARE modelled deliberately,
// because the restore depends on them and getting them wrong is the bug:
//
//   * `createMcpServer` caches no tools and `setMcpServerEnabled(true)` is what
//     lists them (`mcp-servers.service.ts`: `toolsJson: null` on create), and
//   * `createAutopilot` refuses an `allowedMcpTools` entry that no cached tool
//     list resolves, with `AUTOPILOT_MCP_TOOL_UNKNOWN` — the error that lost
//     both autopilots three times out of three.
'use strict';

const http = require('node:http');

let minted = 0;
const freshId = () => `fake-${(minted += 1)}`;
const json = (response, body) => {
  response.writeHead(200, { 'Content-Type': 'application/json' });
  response.end(JSON.stringify(body));
};

/**
 * @param seed.tenants      tenant names, in order
 * @param seed.events       event names this installation publishes
 * @param seed.sources      `datasourceId`s mounted on the deployment
 * @param seed.mcpCatalogue slug → the tools that server answers with, or absent
 *                          for a server that refuses the probe
 */
function fakeStand(seed = {}) {
  const store = {
    tenants: (seed.tenants ?? ['Cypress Bay Digestive Health', 'Latica', 'Pharma Partner'])
      .map((name) => ({ id: freshId(), name })),
    events: (seed.events ?? ['signal.created', 'signal.recurred'])
      .map((name) => ({ id: freshId(), name, systemKey: name })),
    sources: (seed.sources ?? []).map((datasourceId) => ({
      id: freshId(), tenantId: null, datasourceId, name: datasourceId,
      dataSourceProviderId: freshId(), schemaTableCount: 4,
    })),
    models: new Map(),
    libraryItems: [],
    libraryFolders: [],
    dashboards: [],
    autopilots: [],
    mcpServers: [],
  };
  const catalogue = new Map(Object.entries(seed.mcpCatalogue ?? {}));
  /** Every mutation the restore performed, in order. The idempotency assertion
   *  is `calls.filter(isMutation).length === 0` on the second run. */
  const calls = [];

  const EMPTY_MODEL = { version: 1, datasets: [], dimensions: [], hierarchies: [], bindings: [], metrics: [] };
  const tenantOf = (id) => store.tenants.find((one) => one.id === id);
  const itemOf = (id) => store.libraryItems.find((one) => one.id === id);
  const boardOf = (id) => store.dashboards.find((one) => one.id === id);
  const serverOf = (id) => store.mcpServers.find((one) => one.id === id);
  const boundNames = () => new Set(store.mcpServers.flatMap((one) => (one.tools ?? []).map((t) => t.boundName)));

  /** A dashboard as the DASHBOARDS query returns it. */
  const boardOut = (board) => ({
    ...board,
    items: (board.layout.items ?? []).map((cell) => {
      const item = itemOf(cell.libraryItemId);
      return { id: cell.id, title: item?.title ?? null };
    }),
  });

  const subscriptionsOut = (subs) => (subs ?? []).map((one) => ({
    ...one,
    eventName: store.events.find((event) => event.id === one.eventId)?.name ?? one.eventId,
  }));

  /** `[detector, handler]`, mutations first: a mutation document mentions the
   *  query fields it returns, so the order is what disambiguates them. */
  const OPERATIONS = [
    ['adminSemanticModelImport(', ({ doc, t }) => {
      store.models.set(t, doc);
      return { adminSemanticModelImport: { created: (doc.datasets ?? []).length, updated: 0, deleted: 0, calendar: null, invalidatedItems: [] } };
    }],
    ['createLibraryFolder(', ({ name, parentId }) => {
      const folder = { id: freshId(), name, parentId: parentId ?? null, position: store.libraryFolders.length, itemCount: 0 };
      store.libraryFolders.push(folder);
      return { createLibraryFolder: folder };
    }],
    ['saveToLibrary(', ({ i }) => {
      const item = {
        id: freshId(), version: 1, ownerId: 'fake-owner',
        description: null, visibility: 'TENANT', kind: null, query: null, parameterValues: null,
        customSql: null, customBindings: null, datasourceId: null, aiContext: null, modelId: null,
        sourceThreadId: null, sourceMessageId: null, tags: null, folderId: null,
        runDatasetCode: null, runDataAsOf: null,
        createdAt: '2026-11-25T00:00:00Z', updatedAt: '2026-11-25T00:00:00Z',
        ...i,
      };
      store.libraryItems.push(item);
      return { saveToLibrary: { id: item.id, title: item.title } };
    }],
    ['updateLibraryItem(', ({ id, i }) => {
      const item = itemOf(id);
      // `changeDescription` is a note on the version, not a field of the item.
      const { changeDescription, ...patch } = i;
      Object.assign(item, patch, { version: item.version + 1 });
      return { updateLibraryItem: { id: item.id, title: item.title } };
    }],
    ['moveLibraryItems(', ({ ids, folderId }) => {
      for (const id of ids) itemOf(id).folderId = folderId ?? null;
      return { moveLibraryItems: ids.length };
    }],
    ['deleteLibraryItem(', ({ id }) => {
      store.libraryItems = store.libraryItems.filter((one) => one.id !== id);
      return { deleteLibraryItem: true };
    }],
    ['createDashboard(', ({ i }) => {
      const board = {
        id: freshId(), description: null, about: null, visibility: 'TENANT', tags: null,
        refreshTtlSeconds: null, autoRefreshOnData: null, periodAnchor: null, ownerId: 'fake-owner',
        createdAt: '2026-11-25T00:00:00Z', updatedAt: '2026-11-25T00:00:00Z',
        layout: { items: [], version: 1 }, filters: [], widgetFilterBindings: [], eventSubscriptions: [],
        ...i,
      };
      store.dashboards.push(board);
      return { createDashboard: { id: board.id, title: board.title } };
    }],
    ['updateDashboard(', ({ id, i }) => {
      const board = boardOf(id);
      const { eventSubscriptions, ...patch } = i;
      Object.assign(board, patch);
      if (eventSubscriptions !== undefined) {
        board.eventSubscriptions = eventSubscriptions.map((one) => ({ ...one, daysOfWeek: one.daysOfWeek ?? null }));
      }
      return { updateDashboard: { id: board.id } };
    }],
    ['deleteDashboard(', ({ id }) => {
      store.dashboards = store.dashboards.filter((one) => one.id !== id);
      return { deleteDashboard: true };
    }],
    ['addItemToDashboard(', ({ d, i, p }) => {
      const board = boardOf(d);
      const cell = { id: freshId(), libraryItemId: i, position: { ...p }, filterBindingsAdopted: true, entityBindingsAdopted: true };
      board.layout.items.push(cell);
      board.widgetFilterBindings.push({ layoutItemId: cell.id, appliedFilterValues: null, slots: [], bindings: [] });
      return { addItemToDashboard: { id: board.id, layout: board.layout } };
    }],
    ['removeItemFromDashboard(', ({ d, l }) => {
      const board = boardOf(d);
      board.layout.items = board.layout.items.filter((one) => one.id !== l);
      board.widgetFilterBindings = board.widgetFilterBindings.filter((one) => one.layoutItemId !== l);
      return { removeItemFromDashboard: { id: board.id } };
    }],
    ['updateDashboardLayout(', ({ id, layout }) => {
      boardOf(id).layout = layout;
      return { updateDashboardLayout: { id } };
    }],
    ['addDashboardFilter(', ({ d, i }) => {
      const board = boardOf(d);
      const filter = {
        id: freshId(), dimension: null, hierarchy: null, label: null, type: null, defaultValue: null,
        defaultLatest: false, hidden: false, order: board.filters.length,
        dimensionId: null, dimensionDisplayName: null, dimensionKind: null, timeRole: null,
        hierarchyId: null, hierarchyDisplayName: null, levels: null,
        ...i,
      };
      board.filters.push(filter);
      return { addDashboardFilter: { id: board.id, filters: board.filters } };
    }],
    ['updateDashboardFilter(', ({ d, f, i }) => {
      Object.assign(boardOf(d).filters.find((one) => one.id === f), i);
      return { updateDashboardFilter: { id: d } };
    }],
    ['removeDashboardFilter(', ({ d, f }) => {
      const board = boardOf(d);
      board.filters = board.filters.filter((one) => one.id !== f);
      return { removeDashboardFilter: { id: board.id } };
    }],
    ['setWidgetFilterBinding(', ({ d, l, f, p, lv }) => {
      const widget = boardOf(d).widgetFilterBindings.find((one) => one.layoutItemId === l);
      widget.bindings = [
        ...widget.bindings.filter((one) => one.filterId !== f),
        { filterId: f, state: 'BOUND', parameter: p ?? null, via: null, level: lv ?? null },
      ];
      return { setWidgetFilterBinding: { id: d } };
    }],
    ['createAutopilot(', ({ i }) => {
      const unknown = (i.allowedMcpTools ?? []).filter((name) => !boundNames().has(name));
      if (unknown.length > 0) {
        return { errors: [{ message: `Unknown MCP tools: ${unknown.join(', ')}.`, extensions: { code: 'AUTOPILOT_MCP_TOOL_UNKNOWN', unknown } }] };
      }
      const { eventSubscriptions, ...rest } = i;
      const autopilot = {
        id: freshId(), modelId: null, enabled: false, cronExpression: null, visibility: 'TENANT',
        signalMode: false, signalBlock: null, allowedMcpTools: [], parameters: null, ownerId: 'fake-owner',
        createdAt: '2026-11-25T00:00:00Z', updatedAt: '2026-11-25T00:00:00Z',
        ...rest,
        eventSubscriptions: (eventSubscriptions ?? []).map((one) => ({
          eventId: one.eventId,
          daysOfWeek: one.daysOfWeek ?? null,
          signalFilter: one.signalFilter
            ? { autopilotIds: null, minPriority: null, codes: null, scope: null, ...one.signalFilter }
            : null,
        })),
      };
      store.autopilots.push(autopilot);
      return { createAutopilot: { id: autopilot.id, name: autopilot.name } };
    }],
    ['updateAutopilot(', ({ id, i }) => {
      const autopilot = store.autopilots.find((one) => one.id === id);
      const unknown = (i.allowedMcpTools ?? []).filter((name) => !boundNames().has(name));
      if (unknown.length > 0) {
        return { errors: [{ message: `Unknown MCP tools: ${unknown.join(', ')}.`, extensions: { code: 'AUTOPILOT_MCP_TOOL_UNKNOWN', unknown } }] };
      }
      const { eventSubscriptions, ...rest } = i;
      Object.assign(autopilot, rest);
      if (eventSubscriptions !== undefined) {
        autopilot.eventSubscriptions = eventSubscriptions.map((one) => ({
          eventId: one.eventId,
          daysOfWeek: one.daysOfWeek ?? null,
          signalFilter: one.signalFilter
            ? { autopilotIds: null, minPriority: null, codes: null, scope: null, ...one.signalFilter }
            : null,
        }));
      }
      return { updateAutopilot: { id: autopilot.id, name: autopilot.name } };
    }],
    ['deleteAutopilot(', ({ id }) => {
      store.autopilots = store.autopilots.filter((one) => one.id !== id);
      return { deleteAutopilot: true };
    }],
    ['checkMcpServer(', ({ i }) => {
      const slug = i.slug ?? serverOf(i.id)?.slug;
      const tools = catalogue.get(slug);
      if (!tools) {
        return { checkMcpServer: { ok: false, errorCode: 'MCP_UNREACHABLE', error: `"${slug}" answered with an HTTP error (403).`, tools: [], skipped: [] } };
      }
      return { checkMcpServer: { ok: true, errorCode: null, error: null, tools, skipped: [] } };
    }],
    ['createMcpServer(', ({ i }) => {
      const server = {
        id: freshId(), tenantId: store.tenants[0].id, global: false, enabledForTenant: true,
        description: null, enabled: false,
        hasAuth: Boolean(i.authHeaders?.length),
        authHeaderNames: (i.authHeaders ?? []).map((one) => one.name),
        // Create caches nothing. Enabling is what lists the tools.
        tools: [], toolCount: 0, toolsCachedAt: null, lastCheckedAt: null, lastError: null, editable: true,
        createdAt: '2026-11-25T00:00:00Z', updatedAt: '2026-11-25T00:00:00Z',
        name: i.name, slug: i.slug, url: i.url,
        ...(i.description === undefined ? {} : { description: i.description }),
      };
      store.mcpServers.push(server);
      return { createMcpServer: { id: server.id, name: server.name, slug: server.slug, url: server.url, enabled: server.enabled } };
    }],
    ['updateMcpServer(', ({ i }) => {
      const server = serverOf(i.id);
      const { id, authHeaders, ...patch } = i;
      const repointed = patch.url !== undefined && patch.url !== server.url;
      Object.assign(server, patch);
      if (authHeaders !== undefined) {
        server.hasAuth = authHeaders.length > 0;
        server.authHeaderNames = authHeaders.map((one) => one.name);
      }
      // A cache read with another token, or from another address, is stale by
      // definition — the service drops it, and so does this.
      if (authHeaders !== undefined || repointed) {
        server.tools = [];
        server.toolCount = 0;
      }
      return { updateMcpServer: { id: server.id, slug: server.slug, url: server.url, enabled: server.enabled } };
    }],
    ['setMcpServerEnabled(', ({ id, enabled }) => {
      const server = serverOf(id);
      server.enabled = enabled;
      if (enabled) {
        const tools = catalogue.get(server.slug);
        if (!tools) {
          return { errors: [{ message: `"${server.slug}" answered with an HTTP error (403).`, extensions: { code: 'MCP_UNREACHABLE' } }] };
        }
        server.tools = tools;
        server.toolCount = tools.length;
        server.lastCheckedAt = '2026-11-25T00:00:00Z';
      }
      return { setMcpServerEnabled: { id: server.id, enabled: server.enabled } };
    }],
    ['deleteMcpServer(', ({ id }) => {
      store.mcpServers = store.mcpServers.filter((one) => one.id !== id);
      return { deleteMcpServer: true };
    }],
    // Queries.
    ['adminSemanticModelExport(', ({ t }) => ({
      adminSemanticModelExport: store.models.get(t) ?? EMPTY_MODEL,
    })],
    ['adminDataSources(', () => ({ adminDataSources: { items: store.sources } })],
    ['availableEvents', () => ({ availableEvents: store.events })],
    ['adminTenants', () => ({ adminTenants: store.tenants })],
    ['libraryFolders', () => ({ libraryItems: store.libraryItems, libraryFolders: store.libraryFolders })],
    ['widgetFilterBindings', () => ({ dashboards: store.dashboards.map(boardOut) })],
    ['allowedMcpTools', () => ({
      autopilots: store.autopilots.map((one) => ({ ...one, eventSubscriptions: subscriptionsOut(one.eventSubscriptions) })),
    })],
    ['mcpServers', () => ({ mcpServers: store.mcpServers })],
  ];

  /** Which of the operations above CHANGE the stand, named rather than guessed
   *  from the word they start with. `checkMcpServer` is a GraphQL mutation and
   *  is deliberately not here: handed a slug and a URL and no `id`, it opens a
   *  connection, lists, and persists neither a tool cache nor a `lastError`, so
   *  the restore is entitled to call it while validating. */
  const MUTATIONS = new Set([
    'adminSemanticModelImport(', 'createLibraryFolder(', 'saveToLibrary(', 'updateLibraryItem(',
    'moveLibraryItems(', 'deleteLibraryItem(', 'createDashboard(', 'updateDashboard(',
    'deleteDashboard(', 'addItemToDashboard(', 'removeItemFromDashboard(', 'updateDashboardLayout(',
    'addDashboardFilter(', 'updateDashboardFilter(', 'removeDashboardFilter(',
    'setWidgetFilterBinding(', 'createAutopilot(', 'updateAutopilot(', 'deleteAutopilot(',
    'createMcpServer(', 'updateMcpServer(', 'setMcpServerEnabled(', 'deleteMcpServer(',
  ]);
  for (const token of MUTATIONS) {
    if (!OPERATIONS.some(([candidate]) => candidate === token)) {
      throw new Error(`${token} is listed as a mutation and not implemented`);
    }
  }

  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://fake');
    // The sign-in dance `applogin.js` performs: a form, then a session cookie.
    if (url.pathname === '/oauth2/start') {
      response.writeHead(200, { 'Content-Type': 'text/html' });
      response.end('<html><body><form action="/fake-login" method="post"></form></body></html>');
      return;
    }
    if (url.pathname === '/fake-login') {
      response.writeHead(200, { 'Set-Cookie': '_oauth2_proxy=fake-session; Path=/', 'Content-Type': 'text/html' });
      response.end('<html><body>signed in</body></html>');
      return;
    }
    if (url.pathname !== '/graphql') {
      response.writeHead(404);
      response.end();
      return;
    }
    let body = '';
    request.on('data', (chunk) => { body += chunk; });
    request.on('end', () => {
      const { query, variables } = JSON.parse(body);
      const found = OPERATIONS.find(([token]) => query.includes(token));
      if (!found) {
        json(response, { errors: [{ message: `the fake stand does not implement: ${query.slice(0, 120)}` }] });
        return;
      }
      const [token, handler] = found;
      calls.push({ token, mutation: MUTATIONS.has(token), variables });
      try {
        const answered = handler(variables ?? {});
        // A handler that answers with `errors` is the API refusing, which is
        // what `graphql()` in applogin.js turns into a thrown error.
        json(response, answered.errors ? answered : { data: answered });
      } catch (error) {
        json(response, { errors: [{ message: `the fake stand broke: ${error.message}` }] });
      }
    });
  });

  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      resolve({
        url: `http://127.0.0.1:${server.address().port}`,
        store,
        calls,
        mutations: () => calls.filter((one) => one.mutation),
        reset: () => { calls.length = 0; },
        close: () => new Promise((done) => server.close(done)),
      });
    });
  });
}

module.exports = { fakeStand };
