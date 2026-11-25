# The stand's configuration, as it stood on 2026-11-25

Everything here was configured **by hand, inside the running deployment**, and lives in that
deployment's own PostgreSQL. It is in no manifest, no listing and no platform backup: a published
cluster space is frozen, so there is no `exec` and no `pg_dump`, and a re-created deployment starts
with an empty database. **These files are the only copy.**

| | |
|---|---|
| Deployment | `https://analyst-stand.conf-apps.superprotocol.dev` — Conversational Analyst listing **5.0.2**, agent **1.22.1** |
| Cloud | `azure-test`, marketplace deployment `f965fb0a-2cbf-40ea-9a52-43cb962820a9` |
| Exported | **2026-11-25**, see `MANIFEST.json` for the exact timestamp. Re-taken on SUP-939 so the export carries the tool server; nothing else had changed since 2026-11-23 but one autopilot's `updatedAt` |
| Taken by | `harness/export-stand.js`, read-only — every call a GraphQL **query**, through the stand's own oauth2-proxy front door |
| Account | the installation administrator (`MANIFEST.json` → `scopedTo.user`) |

## What is here

| File | |
|---|---|
| `semantic-model.cypress-bay-digestive-health.json` | 4 datasets, 7 dimensions, 6 metrics, 1 hierarchy — the clinic's model over `cbdh_*` |
| `semantic-model.latica.json`, `semantic-model.pharma-partner.json`, `semantic-model.clinical-research-network.json` | empty, and recorded as empty on purpose: the three other tenants have no model here, and an absent file would leave that ambiguous. The researcher tenant exists on this deployment too (it was created here before it got its own) and its model lives in `researcher-configuration-backup/` |
| `library-items.json` | 15 charts and tables — title, visualization spec, semantic query, custom SQL, bindings, tags |
| `library-folders.json` | none exist |
| `dashboards.json` | 2 dashboards — layout and geometry, filters, widget filter bindings, event subscriptions, and which items each holds. Both currently have **no** filters and **no** event subscriptions; the 12 entries under `widgetFilterBindings` are one per widget and their `slots` are *derived* from each library item's own query, not configuration somebody chose. `bindings` beside them — the slot-to-filter wiring, which **is** configuration — is empty for the same reason: with no dashboard filters there is nothing to wire |
| `autopilots.json` | *Surveillance overdue* and *Book the recall* — prompt templates, schedule, signal mode, tool allowlist, subscriptions |
| `mcp-servers.json` | the clinic's tool server registration — name, slug, address, the egress wording it carries as its description, whether it is switched on, and the tools it resolved. **Added on SUP-939**: an export taken before 2026-11-25 does not have it, and the restore treats its absence as "no tool server", not as an error. Every file beside it predates that and is unchanged |
| `MANIFEST.json` | provenance, counts, and what the export was scoped to |

### Where the MCP server's configuration lives, exactly

It was the one piece a rebuild lost outright, so it is worth being precise about.

The **registration** is tenant configuration in the deployment's own PostgreSQL, created through
Administration → MCP servers — which is what `harness/mcpreg.js` drives — so it is in
`mcp-servers.json` in full and `restore-stand.js` replays it. It is *not* in the signed snapshot,
and the finding in the stand's own README says why that is the honest description.

The **token** is not, and cannot be: it is the bearer Cypress Bay issued, the API offers no read of
an auth header's value at all, and only the header's NAME is exported. It lives in exactly two
places, neither of them this repository:

| | |
|---|---|
| `clinic.token` beside the harness | what the operator passes as `MCP_TOKEN_FILE`; `stands/latica/.gitignore` ignores `*.token` |
| `/etc/clinic-scheduling/grants.json` on the marketplace host, mode `0400` | the clinic's own list of the tokens it issued, and where a replacement is read from |

The listing's `mcpServerName` / `mcpServerUrl` parameters are a **different** mechanism: they
render into the attested ConfigMap as a `global` server, there is no create mutation behind one,
and setting them moves the attested digest and costs the stand its gated data (finding 1 in the
stand's README). The export records the `global` flag and the restore skips those rows.

## What is deliberately not here

- **No credentials of any kind.** `adminDataSourceCredentials` is never called; the data-source
  connection password is a deployment parameter and stays in the marketplace. `lastDatasetToken`
  and `thumbnailUrl` are stripped on the way out by `DROP` in the export script — a dataset token
  is a bearer for the rows behind it.
- **No data.** The clinic's rows are in ClickHouse on the gatekeeper host and are regenerated from
  `datasets/cypressbay-digestive-health/generate/generate.py`.
- **No version history.** A library item's and a dashboard's saved versions have no read API that
  returns their full definition; each item is captured at its current version.
- **No dashboard run results.** A widget's last dataset is behind a token, and the token is a
  bearer for the rows; the restore re-runs the widgets instead.
- **Dashboards, library items and autopilots are one account's view.** There is no cross-tenant
  admin read for them, so what was exported is what the installation administrator can see in the
  tenant it belongs to — the clinic's. The semantic model *is* read per tenant, for all three.

## Re-taking the export

Read-only, safe against the live stand, and the way to refresh these files after somebody
configures something else:

```bash
cd stands/latica
APP_BASE=https://analyst-stand.conf-apps.superprotocol.dev \
APP_USER=<installation admin> PASS_FILE=./stand.administratorPassword \
OUT=./configuration-backup node harness/export-stand.js
```

`PASS_FILE` is a path, never an argument. The administrator password is readable from the
marketplace with `harness/`-adjacent `creds.js` in the SUP-712 working directory; it is not in this
repository and must not be put here.

## Restoring it into a rebuilt stand

> **This is for a stand that has been re-created.** The deployment these files came from must not be
> re-created, unpublished, reconfigured or restarted — its owner configured it by hand for a
> demonstration and said so on SUP-908. `harness/restore-stand.js` refuses
> `analyst-stand.conf-apps.superprotocol.dev` by name for that reason.

### 0. Before anything

The rebuilt deployment must already be:

- deployed from `apps/conversational-analyst` **on the same parameter set** (`harness`-adjacent
  `params.js` in the SUP-712 working directory is the reference set the attested digest is measured
  with — a different parameter set attests a different digest and the gate then refuses the data);
- holding the three tenants *Cypress Bay Digestive Health*, *Latica*, *Pharma Partner* — the
  restore matches each model file to the tenant of the same **name**, because a rebuild has new
  tenant ids;
- connected to the clinic's data source, with the gate admitting it (`datasets/cypressbay-digestive-health`);
- carrying its org-admin accounts (`harness/orgadmins.js` in the SUP-712 working directory).

### 1. Run the restore

```bash
cd stands/latica
APP_BASE=https://<the rebuilt stand> \
CONFIRM_TARGET=https://<the rebuilt stand> \
APP_USER=<installation admin> PASS_FILE=./stand.administratorPassword \
MCP_TOKEN_FILE=./clinic.token \
KC_BASE=https://<the rebuilt stand>/auth KC_PASS_FILE=./<stand>.internalSecret \
APP_HOST=<the rebuilt stand's host> RESEARCHER_PASS_FILE=./researcher.password \
IN=./configuration-backup node harness/restore-stand.js

# first, always: validate and print the plan, write nothing
… DRY_RUN=yes node harness/restore-stand.js

# and only when you mean it: also remove what the export does not account for
… PRUNE=yes   node harness/restore-stand.js
```

**It reconciles — it does not create.** Every list is matched against what the target already
holds, by the stable name a person chose, and only the difference is written. Run it twice and the
second run reports nothing to create and nothing to update; that is asserted by
`harness/tests/reconcile.test.js` against this very export, and it is the property whose absence
cost the SUP-917 rehearsal 37 library items and 4 dashboards. What it cannot match it says so
about: a renamed item is a create and an extra, and an extra is **left alone** unless `PRUNE=yes`,
because the restore does not delete what it cannot identify.

**And it validates before it mutates.** Every precondition is a read — the export agrees with its
own manifest, the tenants exist or can be created, Keycloak's admin API answers, the three release
cards are mounted on the deployment, the clinic's tool server answers a tool list and offers the
tools the autopilots name, every subscribed event exists. On a failed precondition it prints all of
them and exits, and **nothing has been changed**. The checks that need nothing but the files are
answered before it even signs in.

It applies, in this order and for this reason:

1. **the semantic model**, with `adminSemanticModelImport(mode: replace)` — the product's own
   import, the exact counterpart of the export. It must go first: every `kind: semantic` library
   item names dataset, dimension and metric **codes**, and `replace` marks as invalid any item
   whose query names an entity the import removed;
2. **the library items**, with `saveToLibrary` — the export's fields are a superset of
   `SaveToLibraryInput`, so each item is recreated from its own definition. New ids are
   remembered;
3. **the dashboards**, with `createDashboard`, then `addItemToDashboard` per widget at its exported
   `position`, then `addDashboardFilter` (and `updateDashboardFilter` for `order`, which
   `AddDashboardFilterInput` has no field for), then `setWidgetFilterBinding` for the slot-to-filter
   wiring, then `updateDashboard` for the refresh and period settings and the event subscriptions.
   **Every identifier in a dashboard is new on a rebuild** — the library item ids changed in step 2,
   `addItemToDashboard` mints a fresh layout-item id, `addDashboardFilter` mints a fresh filter id
   — so each is restored by mapping old to new. It is the arrangement being restored, not the
   identifiers;
4. **the tool servers**, with `createMcpServer` and then `setMcpServerEnabled` — and **before** the
   autopilots, which is the whole reason this step exists. `createMcpServer` stores no tool list;
   *enabling* is what reads the tools and caches them, and that cache is what step 5 resolves an
   `allowedMcpTools` entry against. The cache is read back before step 5 starts, so if the tool is
   still unresolvable the run stops with everything else in place rather than losing the
   autopilots;
5. **the autopilots**, with `createAutopilot` or `updateAutopilot` by name. `harness/autopilots.js`
   is the better path when `autopilots/*.njk` is current — it creates the stand's two from their
   templates, and the templates are reviewed; this replays whatever the export captured.

### 2. What is replayed, what is regenerated, and what is not

| | |
|---|---|
| **Replayed** | semantic model (per tenant, by tenant *name*); library items with their specs, queries, custom SQL, bindings and tags; dashboards with their title/description/about/visibility/tags, **widget geometry**, filters (`dimension`, `hierarchy`, `label`, `type`, `defaultValue`, `hidden`, `order`), widget filter bindings (`parameter`, `level`), `refreshTtlSeconds`, `autoRefreshOnData`, `periodAnchor`, event subscriptions; autopilots with prompt, schedule, signal mode, tool allowlist and subscriptions |
| **Regenerated, not replayed** | a widget's filter **slots** — `addItemToDashboard` derives them from the library item's own query, so replaying them would be writing down an answer the product computes; a library item's `version`, which starts again at 1; a widget's dataset and `lastRefreshedAt`, which come back on the first refresh |
| **Cannot be restored — do it by hand** | a filter's **`defaultLatest`**: neither `AddDashboardFilterInput` nor `UpdateDashboardFilterInput` has a field for it. The restore prints a line naming each filter that had it. A hierarchy filter's **`levels`** are not settable either, but they follow from the hierarchy the filter names, so they come back on their own. Version history, in either place |

### 2a. Rehearsing it on a throwaway, which is how the procedure is proved

The restore is proved by rebuilding a **throwaway** deployment and diffing its export against the
one it was restored from. That is what found all three of the defects SUP-939 fixed, and it is what
has to pass before either real stand is re-created. It costs a cluster space, so it is a **paid
`terraform apply`** and needs a human go-ahead (team workflow §6) — the plan, not the run, is the
part that belongs in a repository.

```bash
# 1. a fresh export of the real stand, FIRST and always (read-only)
APP_BASE=https://analyst-stand.conf-apps.superprotocol.dev APP_USER=… PASS_FILE=… \
  OUT=./before node harness/export-stand.js

# 2. the throwaway, on its own hostname, same parameter set, same release cards
#    as the real stand. The reference set is the one the digest is measured with;
#    a different set attests a different digest and the gate then refuses the data.
DEPLOY_NAME=analyst-rehearsal-503 node create.js      # the SUP-712 working directory

# 3. the clinic approves the throwaway's digest — BEFORE the restore, not after.
#    An ADDITIONAL entry in the receiver's grants.json; the real stand's own
#    approval is not touched. Without it the tool server answers 403, its tools
#    cannot be cached, and the autopilots cannot be created at all — which the
#    restore now refuses in advance and says so.

# 4. restore, then export the throwaway and compare
APP_BASE=https://<throwaway> CONFIRM_TARGET=https://<throwaway> … \
  IN=./before DRY_RUN=yes node harness/restore-stand.js    # read the plan first
APP_BASE=https://<throwaway> … IN=./before node harness/restore-stand.js
APP_BASE=https://<throwaway> … OUT=./after node harness/export-stand.js
BEFORE=./before AFTER=./after node harness/diff-exports.js

# 5. and the second pass, which is the other half of the acceptance criterion
APP_BASE=https://<throwaway> … IN=./before DRY_RUN=yes node harness/restore-stand.js

# 6. tear it down: the deployment, its DNS, and the extra grants.json entry
DEPLOY_NAME=analyst-rehearsal-503 node delete.js
```

**The acceptance criterion is two things, and both are needed:**

1. `diff-exports.js` prints **`no differences`** — 15 library items, 2 dashboards, 2 autopilots,
   1 tool server, 4 datasets in the clinic's model, and every definition identical, not just every
   count. It compares the same way the restore reconciles, so a changed definition under an
   unchanged name is a difference; the first rehearsal compared counts, and a count comparison
   cannot see that.
2. The **second** `DRY_RUN` reports every list at `0 to create, 0 to update` and the model at
   `0 to import`. That is the half no offline test can reach: it is the only thing that shows the
   real API's *write* path normalises nothing. A model that arrived through
   `adminSemanticModelImport` and re-exports differently would re-import on every run for ever, and
   `replace` marks as invalid every library item whose query names an entity the import removed.

A failure of either is a finding, not a retry.

### 3. What the restore cannot do, and you have to

- **Re-point the clinic's grant at the new digest, FIRST.** The receiver's `grants.json` names
  `approvedDigest`; a rebuilt deployment attests a different one. Read it from the cloud
  (`GET <cloud>/api/v1/deployments/<id>/evidence`) and write it in, or every call is refused
  `attestation_digest_mismatch` — which is the mechanism working. Until it is done, the restore
  refuses before it writes anything: the clinic's tool server answers the registration probe with
  a `403`, the tool cannot be cached, and an autopilot that names it could not be created. The
  refusal says so and says what to do. **This is the one step that has to happen before the
  restore rather than after it.**
- **Check that the dashboards render.** A widget whose semantic query names a code the import did
  not create comes back empty rather than missing, and that is the one failure mode this procedure
  cannot assert for you.
- **Four library-item fields have no update.** `UpdateLibraryItemInput` carries no
  `parameterValues`, `aiContext`, `modelId` or `type`, so an item that is already on the stand and
  differs only in one of those is reported by name — `! … has no field on UpdateLibraryItemInput;
  set it by hand` — rather than silently left wrong or duplicated. On a fresh rebuild the question
  does not arise: every item is created from the export, and `saveToLibrary` takes all four.
- **A filter's `defaultLatest`**, as before: neither filter input has a field for it.
