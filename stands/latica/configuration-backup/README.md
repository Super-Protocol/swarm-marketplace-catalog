# The stand's configuration, as it stood on 2026-11-23

Everything here was configured **by hand, inside the running deployment**, and lives in that
deployment's own PostgreSQL. It is in no manifest, no listing and no platform backup: a published
cluster space is frozen, so there is no `exec` and no `pg_dump`, and a re-created deployment starts
with an empty database. **These files are the only copy.**

| | |
|---|---|
| Deployment | `https://analyst-stand.conf-apps.superprotocol.dev` — Conversational Analyst listing **5.0.2**, agent **1.22.1** |
| Cloud | `azure-test`, marketplace deployment `f965fb0a-2cbf-40ea-9a52-43cb962820a9` |
| Exported | **2026-11-23**, see `MANIFEST.json` for the exact timestamp |
| Taken by | `harness/export-stand.js`, read-only — every call a GraphQL **query**, through the stand's own oauth2-proxy front door |
| Account | the installation administrator (`MANIFEST.json` → `scopedTo.user`) |

## What is here

| File | |
|---|---|
| `semantic-model.cypress-bay-digestive-health.json` | 4 datasets, 7 dimensions, 6 metrics, 1 hierarchy — the clinic's model over `cbdh_*` |
| `semantic-model.latica.json`, `semantic-model.pharma-partner.json` | empty, and recorded as empty on purpose: the two other tenants have no model, and an absent file would leave that ambiguous |
| `library-items.json` | 15 charts and tables — title, visualization spec, semantic query, custom SQL, bindings, tags |
| `library-folders.json` | none exist |
| `dashboards.json` | 2 dashboards — layout and geometry, filters, widget filter bindings, event subscriptions, and which items each holds. Both currently have **no** filters and **no** event subscriptions; the 12 entries under `widgetFilterBindings` are one per widget and their `slots` are *derived* from each library item's own query, not configuration somebody chose. `bindings` beside them — the slot-to-filter wiring, which **is** configuration — is empty for the same reason: with no dashboard filters there is nothing to wire |
| `autopilots.json` | *Surveillance overdue* and *Book the recall* — prompt templates, schedule, signal mode, tool allowlist, subscriptions |
| `MANIFEST.json` | provenance, counts, and what the export was scoped to |

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
IN=./configuration-backup node harness/restore-stand.js
```

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
4. **the autopilots**, with `createAutopilot`. `harness/autopilots.js` is the better path when
   `autopilots/*.njk` is current — it creates the stand's two from their templates, and the
   templates are reviewed; this replays whatever the export captured.

### 2. What is replayed, what is regenerated, and what is not

| | |
|---|---|
| **Replayed** | semantic model (per tenant, by tenant *name*); library items with their specs, queries, custom SQL, bindings and tags; dashboards with their title/description/about/visibility/tags, **widget geometry**, filters (`dimension`, `hierarchy`, `label`, `type`, `defaultValue`, `hidden`, `order`), widget filter bindings (`parameter`, `level`), `refreshTtlSeconds`, `autoRefreshOnData`, `periodAnchor`, event subscriptions; autopilots with prompt, schedule, signal mode, tool allowlist and subscriptions |
| **Regenerated, not replayed** | a widget's filter **slots** — `addItemToDashboard` derives them from the library item's own query, so replaying them would be writing down an answer the product computes; a library item's `version`, which starts again at 1; a widget's dataset and `lastRefreshedAt`, which come back on the first refresh |
| **Cannot be restored — do it by hand** | a filter's **`defaultLatest`**: neither `AddDashboardFilterInput` nor `UpdateDashboardFilterInput` has a field for it. The restore prints a line naming each filter that had it. A hierarchy filter's **`levels`** are not settable either, but they follow from the hierarchy the filter names, so they come back on their own. Version history, in either place |

### 3. What the restore cannot do, and you have to

- **Register the MCP server** — it is the clinic tenant's own runtime registration, in the
  deployment's database and not in the signed snapshot: `harness/mcpreg.js`.
- **Re-point the clinic's grant at the new digest.** The receiver's `grants.json` names
  `approvedDigest`; a rebuilt deployment attests a different one. Read it from the cloud
  (`GET <cloud>/api/v1/deployments/<id>/evidence`) and write it in, or every call is refused
  `attestation_digest_mismatch` — which is the mechanism working.
- **Check that the dashboards render.** A widget whose semantic query names a code the import did
  not create comes back empty rather than missing, and that is the one failure mode this procedure
  cannot assert for you.
