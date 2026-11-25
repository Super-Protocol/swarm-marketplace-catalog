# Latica demonstration stand — the two scenarios

What the stand is shown doing, how each part was built from stock product mechanics, and what
was measured rather than assumed. The stand itself is SUP-708; the listing it is deployed from is
`apps/conversational-analyst`; the data it runs on is `datasets/cypressbay-digestive-health`.

| | |
|---|---|
| Stand | `https://analyst-stand.conf-apps.superprotocol.dev`, listing **5.0.2**, agent **1.22.1** |
| Clinic | Cypress Bay Digestive Health — six GI sites, 12,000 synthetic patients, 11 tables |
| Model | the production Confidential Router, `qwen/qwen3-coder-next` |
| Attested digest | `d391cb85d822…` = `sha256/05HLhdgiR5fOaf50fBHQhRUF61GdXh4t9NL602zAvzo`, `matchesExpected: true` |
| Scheduling system | `https://cypress-bay-scheduling.demo.superprotocol.dev` — `clinic-scheduling-mcp/` |

Everything below was run on that stand on 2026-11-23 and the outputs are what it produced, not
what it should produce.

## The sentence this stand is required to carry

> Outbound traffic carries only the action the clinic authorised, for the patient it authorised,
> and only from code the clinic verified. Data never leaves.

It is in the application's own UI, on the tool server's entry under **Administration → MCP
servers**, where it reads beside the address the agent may call. It is also in the prompt of the
autopilot that makes the call. **"Nothing leaves the perimeter" is not said anywhere**, and must
not be: the moment an autopilot writes a recall back, it stops being true.

**And exactly how far "from code the clinic verified" reaches, in the same breath.** The clinic's
check binds the approved code to the **hostname** it calls back: it proves that the deployment
answering on `analyst-stand.conf-apps.superprotocol.dev` attests the digest the clinic approved,
over a TLS certificate that the signed evidence itself names. It is the **token** that binds the
individual call, because the MCP leg carries no client certificate — so a holder of the token,
from anywhere, passes the code check while the stand is healthy. Closing that last gap needs
mutual TLS on the MCP leg, or the caller presenting its namespace `evidenceToken` from
`Secret/swarm-pull` for the receiver to resolve at `<cloud>/v1/evidence/<token>`, which the
platform already serves. Say it this way round whenever the sentence above is said; wording that
is stronger than the mechanism is the exact failure this stand exists to avoid.

## Scenario 1 — Chart abstraction

A clinical document is attached to the message box and read whole. Nothing is de-identified
first: every document names a patient, a date of birth and a medical record number, because that
is what a clinical document does, and reading it safely rather than stripping it is the point.

### 1a — abstract an outside-records packet

`scenarios/abstract.txt`, with `corpus/notes/hollingsworth-elmer-outside-colonoscopy-2020-06-10.pdf`
attached. The procedure has a row in `cbdh_procedures` and nothing else: no findings, no
pathology, no surveillance recommendation. Every field asked for is in the prose and in no
column, so an answer can only come from reading the document.

```bash
APP_BASE=https://analyst-stand.conf-apps.superprotocol.dev \
APP_USER=<clinic org-admin> PASS_FILE=./stand.internalSecret \
QUESTION="$(cat scenarios/abstract.txt)" \
FILES="$PWD/corpus/notes/hollingsworth-elmer-outside-colonoscopy-2020-06-10.pdf" \
TAG=s1a node harness/run.js
```

One turn, no tool calls, ~60 s. It returned the table and the JSON object: patient
`Hollingsworth, Elmer`, DOB `09/23/1985`, MRN `CB-1033496`, procedure `PROC-0001268` at Bay
Terrace Regional Medical Center, Boston prep 5 of 9, **caecum not reached, abandoned at the
splenic flexure**, two polyps in the descending colon (5 mm flat elevated and 2 mm pedunculated,
both hot snare), two sessile serrated specimens with and without low-grade dysplasia, margins
clear, and the endoscopist's **repeat within 3 months, due 09/10/2020**.

Two fields it got weaker than a clinician would: `adenoma_present` and `advanced_histology` came
back `not stated` where a sessile serrated lesion is a defensible "no", and `risk_tier` came back
`not stated` rather than inferred from the interval. The prompt says to write `not stated` rather
than infer, so this is the instruction working — but say what it is rather than reading it as
abstraction quality.

### 1b — the document contradicts the record

`scenarios/reconcile.txt`, with `corpus/notes/tillerson-nadine-report-2025-10-05.pdf` attached.
This one is the practice's own signed report with a pathology addendum, and the structured row
was keyed in before the addendum.

The run read the document, then wrote its own SQL over `cbdh_procedures`, `cbdh_findings`,
`cbdh_pathology`, `cbdh_surveillance_recommendations` and `cbdh_recalls` through the gate, and
reported the disagreement:

| Field | Document | Record |
|---|---|---|
| `dysplasia_grade` | High-grade (addendum) | Low-grade |
| `advanced_histology` | yes | `0` |
| `interval_months` | 36 | 84 |
| `due_date` | 2028-10-05 | 2032-10-05 |

It named the document as the source to believe, because the addendum is signed, and closed with
`ACTION:` naming `cbdh_pathology.dysplasia_grade` as the field to correct — which is the right
root: every other disagreement is derived from it. It called the resulting risk tier
*Intermediate* where the generator's guideline calls it *High*; the interval, which is what the
recall turns on, it got right.

Four more planted contradictions are listed in the corpus `MANIFEST.md`, regenerated with
`datasets/cypressbay-digestive-health/generate/generate.py`.

## Scenario 2 — Colonoscopy surveillance recall

Two autopilots, a signal between them, and one MCP call out. Nothing here is application code.

```
Surveillance overdue ──signalEmit──▶ SURV-01 · P1 · risk ──signal.created──▶ Book the recall
  signal mode on                     area = site + patient                    mcp_scheduling_schedule_recall
  cron 10 7 * * 1-5                  merged on repeat                                  │
  or Run now                                                                            ▼
                                                                        Cypress Bay scheduling system
                                                                   token + pull attestation, then booked
```

### The two autopilots

`autopilots/surveillance-overdue.njk` and `autopilots/book-recall.njk`, created by
`harness/autopilots.js`. The first has **signal mode** on, a weekday schedule and one run
parameter (`site`); the second has no schedule, a **subscription to `signal.created` filtered to
code `SURV-01`**, and exactly one tool in its allowlist — `mcp_scheduling_schedule_recall`.

The condition is in the prompt template, not in code: the recommendation must be the current one
(`superseded_by` empty), the recall must be live (`overdue` or `lost_to_follow_up`), the due date
past, the patient active. 1,424 of the clinic's 14,294 recalls qualify.

### The area, and a vocabulary that does not fit

A signal's scope keys are a closed retail list — `retailer, supplier, category, brand, sku,
region, format, dc, store, promo`. A clinic and a patient are not in it, so the area is carried
as `store` = the site and `sku` = the medical record number, with `scopeLabels` giving the site
name and the patient's name, which is what the card and the feed display. It works and it reads
correctly to a viewer; it is a product gap worth naming rather than hiding — a clinical
installation needs its own scope vocabulary.

A scope value is **normalised and lower-cased** before it is stored (`cb-1056691`), and the
clinic's `mrn` column is not, so the subscribed autopilot compares case-insensitively and takes
what it writes back from the chart row, not from the scope. The first run of this scenario failed
on exactly that and booked nothing.

### What it did

| Run | Source | Outcome |
|---|---|---|
| 15:11 | Manual | published `SURV-01` for **Ivey, Dwight · Coral Ridge GI Associates · 2,777 days overdue** |
| 15:17 | Manual, `args.site = Palmetto Point Gastroenterology` | published `SURV-01` for **Abernathy, Marcia · 2,758 days** |
| 15:19 | **Event: Signal created** | booked `RC-0013565` → `APPT-F2B7E9B1`, 2026-11-30 08:00 |
| 15:23 | Manual | the same recall again → `already_booked`, same appointment |
| 15:27 | **Schedule** | the same condition again → `SURV-01` for Ivey **updated**, occurrences 2 |
| 15:30 | Manual | booked `RC-0009629` → `APPT-BFA200B6`, 2026-12-01 08:40 |

Unattended on a schedule and on demand, both. The schedule is `10 7 * * 1-5`; it was temporarily
set to `*/3 * * * *` to observe a `SCHEDULE` run inside the session and put back afterwards.

### Signal merging, verified rather than assumed

The fingerprint is `sha256(v1 | tenant | CODE | canonical scope)` and **`period` is not part of
it**, so the same condition seen tomorrow lands on the same card. Measured: the second run for
the same site and patient produced `outcome: updated`, `occurrences: 2`, one `UPDATED` event on
the existing signal, the same signal id, and a feed total that stayed at 2 — one card per area,
not one per run. The card shows `Repeats: 2 runs` with its first and last firing times.

The subscribed autopilot did **not** run again on that repeat: it is subscribed to
`signal.created`, and a repeat fires `signal.recurred`. So a recurring "surveillance overdue"
updates one card and books nothing twice. If a clinic wants every recurrence acted on, the
subscription list is where that is decided.

## The clinic's scheduling system

`clinic-scheduling-mcp/` — a stateless Streamable-HTTP MCP server with **one tool**,
`schedule_recall`, which writes. There is no read tool, and the diary and the journal are behind
a **separate credential the clinic issues to its own staff**, so nothing the deployment holds can
be used to fetch a patient. See its own README for the attestation protocol and the deployment.

Two independent things admit a call, and the clinic owns both:

1. **The token**, issued by the clinic, sent as `Authorization: Bearer …` on every call. It grants
   one tool and the practice's six sites; a call naming any other site is refused
   (`site_outside_grant`).
2. **The code**, pulled and verified by the clinic **once per 60-second TTL window** (`attest()`
   in `main.mjs`), not on each JSON-RPC frame: it fetches the caller's
   `/.well-known/swarm-evidence`, requires the JWS header to say `RS256`, checks the signature
   against a chain whose every issuer is a CA and whose last link is the pinned
   `Super Swarm Root CA`, checks the signed `hostname`, checks the signed `certFingerprint`
   against the TLS certificate **that** connection presented, and checks `evidenceDigest` against
   the digest the clinic approved. A change of code is therefore caught within the minute, and
   what the check binds is the hostname rather than the caller — see the limitation stated beside
   the required sentence above.

Both the acceptance and the refusal are in the clinic's own append-only journal:

```json
{"outcome":"accepted","tool":"schedule_recall","result":"booked","recall_id":"RC-0009629",
 "appointment_id":"APPT-BFA200B6","attested":{"hostname":"analyst-stand.conf-apps.superprotocol.dev",
 "evidence_digest":"sha256/05HLhdgiR5f…","cert_fingerprint":"sha256/T_ZLhAW13zX…"}}
{"outcome":"refused","reason":"attestation_digest_mismatch",
 "detail":"observed sha256/05HLhdgiR5f…","approvedDigest":"sha256/WITHDRAWN…"}
```

That refusal was produced on the stand by withdrawing the clinic's approval of the digest the
deployment attests to and running the autopilot again. The run reported it and booked nothing:

> The scheduling system returned a 403 HTTP error… **Refusal reason (verbatim):**
> `"cypress-bay-scheduling.demo.superprotocol.dev" answered with an HTTP error (403). Check the URL and the authorization headers.`

Note what the operator is told and what they are not: the agent's MCP client surfaces the status
code, not the body, so **the reason lives in the clinic's journal and not in the operator's run**.
That is the correct trust boundary — the clinic does not owe the operator an explanation — but it
means the demonstration has to show both sides, which is why the journal is a readable endpoint.

## Every outbound destination, in one place

The deployment can reach exactly four things, and three of them are in the attested ConfigMap
(`/data/services.yaml`, attested in full since listing 5.0.0 — credentials are `${NAME}`
references into a Secret, so the document itself is readable out of the signed snapshot):

| Destination | Where it is declared | Attested |
|---|---|---|
| `https://api.router.superprotocol.com/v1` — the model | `services.api.llm.baseURL` | yes |
| `http://analyst-embeddings/v1` — embeddings, in the namespace | `services.api.embeddings.baseUrl` | yes |
| signal webhooks | `services.api.signals.webhook.egress` — allowlist empty, private ranges denied | yes |
| `https://cypress-bay-scheduling.demo.superprotocol.dev/mcp` — the tool server | the tenant's **MCP servers** page | **no** |

The fourth is the one that matters, and it is honest only as written. **Read the finding below
before showing this table to anyone.**

## Findings

### 1. A deployment cannot both declare its own tool server and read gated data

The listing has `mcpServerName` / `mcpServerUrl` parameters that land in
`services.api.mcp.globalServers` — the attested ConfigMap. Setting them is the right way to make
the outbound destination part of the signed snapshot. It was done on the stand, and measured:

| Parameter set | Attested digest | `matchesExpected` | Gate |
|---|---|---|---|
| reference (no tool server) | `d391cb85d822…` | true | `allowed` |
| + `mcpServerName` / `mcpServerUrl` | `1842011deffe…` | **false** | **denied** |
| reverted to reference | `d391cb85d822…` | true | `allowed` |

The gate's verdict, in its own words:

> *Conversational Analyst is running something other than what was approved: it attests to
> `1842011deffe` where its publisher declared `d391cb85d822`.*

The cause is in `gate-reconciler.service.ts`: the admitted client's `expectedDigest` is
`declaredDigestOf(appVersion)` — the **listing's** `evidence.expectedDigest`, with no
per-deployment alternative. A parameter is attested on purpose, so any consumer who chooses a
parameter the reference set did not use attests a different digest and is refused the data. With
the tool server declared, the stand could read nothing: every question came back
`gatekeeper error`.

This is SUP-711's design question arriving as a blocker rather than a presentation problem. Its
answer was recorded as option 2 — *each deployment has its own digest, and the listing's is the
reference configuration's* — and option 2 is **not implementable while gated data is mounted**,
because the gate implements option 1's assumption. Two ways out, both product decisions:

- exclude the consumer-chosen parameters in `evidence.exclude`, so one declared digest fits every
  deployment and what varies is visible in the parameters instead (SUP-711's option 1); or
- give the gate a per-deployment declared digest the **data owner** approves — which is also the
  mechanism the clinic would actually want, since approving code is the data owner's act, not the
  publisher's.

Until one of them exists, the stand runs on the reference parameter set and the tool server is
registered as the clinic organisation's own, through **Administration → MCP servers**. That
registration is real, auditable in the product, and performed by the clinic's administrator —
and it is **in the deployment's database, not in the signed snapshot**. So the enumeration above
is complete and attested for three channels out of four, and the fourth is attested on the
*receiving* side instead: the clinic's grants file names exactly one caller, one tool and six
sites, and refuses everything else.

Being in the database rather than in the snapshot also means a **re-created deployment starts
without it**, and both autopilots then refuse to be created at all, because
`mcp_scheduling_schedule_recall` is not a name anything resolves. That is what happened on the
SUP-917 rehearsal. `export-stand.js` now captures the registration (`mcp-servers.json` —
everything but the bearer token, which has no read at all) and `restore-stand.js` replays it
before the autopilots; `configuration-backup/README.md` says exactly where the token lives and
what has to be approved on the clinic's side first.

### 2. A tenant can add a tool server, and no configuration forbids it

`api.mcp.maxServersPerTenant` is a positive integer with no zero, so an installation cannot say
"the tenants may not add their own". Even with every destination declared in `globalServers`, a
tenant administrator can add another one at runtime, and it will not be in the signed snapshot.
For this stand that is the mechanism being used deliberately; as a product property it is the
thing that stops "every outbound destination is attested" from ever being literally true. A
`tenantServers: false` switch would close it.

### 3. The model is a coder model, and it shows in three small ways

An `evidence.value` longer than 500 characters refuses the whole `signalEmit` call, and the first
version of the prompt asked for the SQL in it: the run found the right patient and published
nothing. A run invented `period.to` as `2025-04-05` until the template was told to use
`{{ now.date }}`. And one run in four ends in `APIError:backend_unavailable` from the router and
succeeds on the retry a minute later — the same flake SUP-905 recorded. None of these is a
confidentiality property; all three are in the run-through so nobody is surprised on camera.

## Running it

`harness/` drives the stand through its own API — the oauth2-proxy front door, then GraphQL — so
a check here exercises what a person would use.

```bash
# The two autopilots, created or updated in place
APP_BASE=… APP_USER=… PASS_FILE=… node harness/autopilots.js

# The tool server, added · checked · switched on, with the egress wording as its description
APP_BASE=… APP_USER=… PASS_FILE=… MCP_URL=https://…/mcp TOKEN_FILE=./clinic.token node harness/mcpreg.js

# Publish a signal and watch the subscribed autopilot act on it
APP_BASE=… APP_USER=… PASS_FILE=… TAG=demo node harness/fire.js
APP_BASE=… APP_USER=… PASS_FILE=… TAG=site ARGS='{"site":"Palmetto Point Gastroenterology"}' node harness/fire.js

# The subscriber on demand, with the signal it would have been handed
APP_BASE=… APP_USER=… PASS_FILE=… ARGS_FILE=./booker-args.json node harness/firebooker.js

# One dialog turn, with documents attached
APP_BASE=… APP_USER=… PASS_FILE=… QUESTION="$(cat scenarios/abstract.txt)" FILES=./note.pdf node harness/run.js

# The stand's hand configuration, out and back in. The export is read-only and safe against the
# live stand; the restore is for a REBUILT one and refuses the live host by name.
APP_BASE=… APP_USER=… PASS_FILE=… OUT=./configuration-backup node harness/export-stand.js
APP_BASE=… CONFIRM_TARGET=<the same> APP_USER=… PASS_FILE=… MCP_TOKEN_FILE=./clinic.token \
  IN=./configuration-backup DRY_RUN=yes node harness/restore-stand.js   # validate and plan
```

`restore-stand.js` reconciles by name rather than creating, registers the clinic's tool server
before the autopilots that need it, and checks every precondition before its first write — see
`configuration-backup/README.md` for the procedure and SUP-939 for what each of those three cost
when it was missing. The matching underneath it is pure and has tests:

```bash
node --test 'stands/latica/harness/tests/*.test.js'     # no network, no credentials, no stand
```

`PASS_FILE` and `TOKEN_FILE` are paths, never arguments: a credential must not reach a shell
history or a process list. Nothing in this directory contains one, and `stands/latica/.gitignore`
is written so that nothing a run produces can become one by accident — the token files the
commands above name, the `*-runs.json` / `*-signals.json` / `autopilot-*.id` the harness writes
beside itself, the screenshots, the generated corpus and the receiver's own state are all
ignored.

### The fourth tenant, and why it is made by hand

The stand has four organisations and the listing can only declare three.
`organizations` in the definition is a fixed three-entry list patched by numeric index — the
specification's patch paths are dot-notation with numeric indices, so a definition cannot append
to a list — and a reconfigure re-renders the version a deployment was *created* from, so a new
listing version reaches only new deployments. The researcher tenant is therefore created directly
in Keycloak:

```bash
KC_BASE=https://analyst-stand.conf-apps.superprotocol.dev/auth \
KC_PASS_FILE=./stand.internalSecret \
APP_HOST=analyst-stand.conf-apps.superprotocol.dev \
RESEARCHER_PASS_FILE=./researcher.password \
  node harness/researcher-tenant.js            # idempotent
  node harness/researcher-tenant.js --check    # read-only
```

`KC_PASS_FILE` is the realm administrator's password, which is the deployment's generated
`internalSecret` — the listing wires `keycloak.auth.adminPassword` to it.

**What makes this honest rather than a shortcut.** The tenant list is **not in the signed
snapshot**: every Secret in the published evidence carries metadata only, no `data`, including
`analyst-keycloak-bootstrap-env` where the three declared organisations ride. So the three tenants
the stand already shows are not attested either, and a fourth created here is no less attested
than they are. Say that plainly in anything written for a viewer — what the snapshot vouches for
is the code; what enforces the tenant boundary is the running Keycloak.

The script mirrors `bootstrap-job.yaml` step for step — the organisation with one domain under the
stand's own host (Keycloak refuses an organisation with no domain and answers 400 without
explaining), the `/org/{orgId}` group with its `admins` and `members` children, and a member — with
one deliberate difference. The bootstrap adds the installation administrator to the **first**
organisation only, so a fourth created to match would be empty and nobody could sign into it. The
researcher gets its **own** user instead, which is also the correct boundary: it must not be a
member of any clinic organisation, and the script **refuses to finish if it is**.

The bootstrap Job only ever adds organisations and never prunes, so this one survives every
re-render. It does not survive a re-created deployment, which is why `restore-stand.js` runs it as
step 0 — before the semantic model, because that is imported per tenant and `adminTenants` only
lists a tenant the realm already has.

### The three aggregate releases, and why the researcher holds only those

Each clinic publishes two cards. One is its record. The other is a release of **counts** — by
site, by month, by procedure type — with no patient, encounter, medical record number or clinician
in it: `cypressbay-release`, `harborpoint-release`, `ridgeline-release`, built by
`datasets/gi-network-generator/aggregate.py`, each about a thirtieth the size of the record it
counts.

They exist because of what a release IS, and that is the whole argument for them: a card that
contains counts and no records cannot produce a record, whoever reads it and however they ask. That
is a property of the data, which needs nothing configured to stay true, and it is a stronger thing
to be able to say than any statement about who may query what.

A deployment also grounds **every** card it mounts into **one** tenant — the deployment's
`primaryOrgAlias`, which on this stand is `cypress-bay` and is an attested parameter — so what a
deployment mounts is a decision about that tenant. The analyst stand therefore mounts
`cypressbay-digestive-health`, which is Cypress Bay's own record in Cypress Bay's own tenant, plus
the three releases, and **not** Harbor Point's or Ridgeline's identifying cards. When those two
came off, the grounding Job's revoke loop did its job:

```
  unregistered source harborpoint_gastroenterology, which is no longer bound
  unregistered source ridgeline_endoscopy, which is no longer bound
```

and the clinic persona that had been listed those sources an hour earlier now gets
*"The data source `harborpoint_gastroenterology` does not exist for this tenant… that data source
contains only aggregate releases"*.

**Say this plainly and never imply otherwise:** the three clinics' *aggregates* are also visible in
the Cypress Bay tenant, because one deployment grounds everything into one tenant. That is
acceptable for counts a clinic chose to release, and it is why the releases exist and the records of
the other two networks do not. A listing cannot yet route one binding to one tenant and another to
another; until it can, "what this deployment mounts" and "what this tenant holds" are the same
sentence, and the way to give two parties different data is to give them different deployments.

### Giving the researcher tenant its sources

Nothing grounds a tenant the listing cannot declare, so this does what `ground.py` does, for that
tenant only:

```bash
APP_BASE=https://researcher-stand.conf-apps.superprotocol.dev \
APP_USER=gate-rehearsal@local.test PASS_FILE=./researcher-stand.administratorPassword \
  node harness/researcher-sources.js --check     # read-only
  node harness/researcher-sources.js             # register, catalogue, annotate
```

It registers each release against **the provider the grounding Job already made**
(`existingProviderId`), so the researcher reads the release through the same gated connection the
deployment already holds and no credential is read, written or printed anywhere — the script never
holds one. Then it
reads each catalogue and annotates every table and column from the dataset card on disk, because
the catalogue gives names and types while the card gives the meanings: what `prep_missing` is for,
that `withdrawal_minutes_total` is divided by `withdrawal_recorded` and never by `procedures`, and
each practice's own coverage caveat. 4 tables and 58 annotations per release. Finally it writes a
`Domain part` system message scoped to that tenant, whose one job is to say that `network_key` is
the same key in all three sources.

Reusing another tenant's provider needs `installation.data-source-providers.write`, which an
organisation administrator does **not** hold — so this is something the **operator** does for the
researcher, and not something the researcher could do for itself. Worth knowing when writing about
the arrangement: which party performed an action is part of what the arrangement is.

### The question that only works pooled

Asked as the researcher, on the researcher's own deployment, against three aggregate sources and
nothing else:

> Compare the adenoma detection rate between the three clinical networks, for screening
> colonoscopies with an adequate bowel preparation, between 2024-01-01 and 2026-11-30. Break it
> down by network and by site. Say how many examinations each network had to exclude and why.

| Network | adenoma / adequate prep | rate | excluded, missing prep |
|---|---|---|---|
| Cypress Bay Digestive Health | 865 / 2,274 | 38.0% | 0 |
| Harbor Point Gastroenterology | 665 / 1,438 | 46.2% | 0 |
| Ridgeline Endoscopy Partners | 292 / 1,105 | 26.4% | **758** |

plus a row per site for all nineteen sites, and the exclusions named: Ridgeline lost 758 of 2,241
structured examinations to missing preparation scores and another 378 to inadequate preparation.

**Two things about those numbers are worth saying out loud.**

They are *exactly* what the release contains — `selftest.py` recomputes this question from the
records and from the counts and requires the two to be equal, at the published size, on every
network.

And they are not the figures the same question produced an hour earlier against the identifying
cards, which were 1,825/4,788 = 38.1%, 1,396/3,052 = 45.7% and 664/2,365 = 28.1% with 1,587
excluded. The data did not change — **the denominator did**. Against the records the agent read
"screening colonoscopies" as every colonoscopy type; against the release, where `procedure_type` is
a published dimension with its five values written down, it read it as screening colonoscopies.
Asked to widen it back out, the researcher reproduces the earlier table to the row:

| Network | adenoma / adequate prep | rate | excluded |
|---|---|---|---|
| Cypress Bay Digestive Health | 1,825 / 4,788 | 38.1% | 0 |
| Harbor Point Gastroenterology | 1,396 / 3,052 | 45.7% | 0 |
| Ridgeline Endoscopy Partners | 664 / 2,365 | 28.1% | 1,587 |

The ranking is the same either way. The lesson is the demonstrable one: a card that names its
dimensions gets a narrower and more defensible denominator than a card that leaves them implicit,
and the only way anybody noticed is that both were asked.

### The researcher's own deployment

The researcher does not share the analyst stand. It has a deployment of its own:

| | |
|---|---|
| Host | `https://researcher-stand.conf-apps.superprotocol.dev` |
| Listing | Conversational Analyst **5.0.2**, the same version the stand runs |
| Mounts | `cypressbay-release`, `harborpoint-release`, `ridgeline-release` — **and nothing else** |
| Tenants | `research-network` (first, so it is the one the grounding Job grounds), `latica`, `pharma` |
| Digest | `d391cb85…bf3a`, `matchesExpected: true` |

**The digest is the same number the stand's deployment attests**, and that is worth knowing before
anybody plans a listing bump around it. Created with a different hostname and a different FIRST
organisation, this deployment measured the same 63 resources and the same digest as the stand:
the hostname is excluded by design — the gate binds evidence to the host it fetched it from — and
the organisation list rides in a Secret, and every Secret in the collected snapshot carries
metadata only. So a parameter is attested when it reaches a manifest, and these two do not. The
set that DOES move it is the pinned charts, which is why a grounding-chart bump needs a fresh
measurement and a hostname does not.

Why a second deployment at all, rather than a fourth tenant on the stand: a deployment grounds
every card it mounts into one tenant, and what a deployment mounts is therefore a decision about
that tenant. Giving two parties different data means giving them different deployments. Here the
researcher's namespace contains no identifying card — so "the researcher holds no clinic record"
is a fact about what exists rather than a claim about a boundary, and it needs nothing configured
to stay true.

Bringing it up, in the order the pieces depend on each other:

```bash
# 1. create it with the three releases bound from the start — a dataset change on a
#    published space costs a window, and the measured digest only includes the egress
#    sidecar's objects when a gateway-mode card is attached
# 2. the deployment's own administrator password is a deployment OUTPUT, not a parameter:
#    a sensitive parameter reads back empty by design
# 3. then the realm, and then its tenant's sources
APP_BASE=https://researcher-stand.conf-apps.superprotocol.dev \
APP_USER=gate-rehearsal@local.test PASS_FILE=./researcher-stand.administratorPassword \
  node harness/researcher-sources.js
```

**Expect to finish the realm bootstrap by hand on a fresh deployment.** On 2026-11-24 this one
came up with the realm half-built: the bootstrap Job's loop over the `identity` service account's
realm-management roles answered `401` on one of the eight, `manage-realm` was the one it lost, and
the rest of the script then uses that service account's own token — so `organizationsEnabled` was
never set, no organisation was ever created, and the grounding Job sat on
`waiting for the identity provider: HTTP Error 403` and then `404`. The Job restarted and failed
the same way each time, so it does not recover on its own. Two scripts close it:

```bash
KC_BASE=https://researcher-stand.conf-apps.superprotocol.dev/auth \
KC_PASS_FILE=./researcher-stand.internalSecret \
  node harness/fix-service-account.js          # CHECK=1 first; reports what is missing
… ADMIN_USERNAME=gate-rehearsal@local.test APP_HOST=researcher-stand.conf-apps.superprotocol.dev \
  ORGANIZATIONS='Clinical Research Network|research-network
Latica|latica
Pharma Partner|pharma' node harness/finish-bootstrap.js
```

`finish-bootstrap.js` mirrors the chart from "realm administrator" onward and is a
create-unless-present at every step, so it is safe on a realm that is partly built and safe to run
twice. The administrator's membership of the FIRST organisation is the step that decides which
tenant gets grounded, so it is the one to check afterwards rather than assume.

Two more things that cost time here and are worth writing down. `KC_ADMIN_PASSWORD` and
`ADMIN_PASSWORD` live in `analyst-keycloak-bootstrap-env`, which is readable with the namespace
kubeconfig; the realm's `internalSecret` is **not** published as a deployment output, so there is
no other way to reach Keycloak's own admin API. And the `administratorPassword` this deployment
published as an output **did not match** the password the realm was seeded with — the one in the
Secret is the one that works. An operator following the console alone could not sign in to this
deployment.

### The cross-source semantic model

The researcher's tenant carries a model over the three releases:
`researcher-configuration-backup/semantic-model.clinical-research-network.json`, six datasets,
three dimensions, **nine bindings**, twelve metrics.

The piece this issue asked for is the `network` dimension, bound to **all three** releases on
`network_key` — one dimension, three bindings — so "by network" means the same thing in each of
them and the member keys line up. `clinical_site` is bound three times too, each through its own
release's sites table, so a site has a name rather than an id whichever network it is in.

**What it cannot do is as useful to know as what it can.** A metric belongs to one dataset, a
dataset belongs to one `datasourceId`, and each release is a separate gated connection whose reader
cannot read the other two — so there is no single pooled metric to define, and a derived one does
not help: the model's own validator says *"a derived metric stays within one dataset"*. Each
network therefore has its own `*_adenoma_detection_rate` and a pooled comparison is three modelled
queries put side by side. Asked for one chart over all three, the agent reported the limitation
itself: *"The model does not support querying across multiple datasets in one semantic query."*

What the model buys, given that, is the thing hand-written SQL could not: a **reusable query**
behind the answer. Before it, the same question was answered from raw SQL and the agent declined
to chart it — *"a chart block with inline data must include a reusable query, which is not
applicable here because the figures came from hand-written SQL over raw tables rather than a
modelled semantic dataset"*. After it, the answer comes back as a chart with a live semantic query
attached, one per network, and the figures are the same: 38.0% / 46.2% / 26.4%.

### The withdrawal beat

The one claim on this stand that has to be *performed* rather than described: the data's owner
controls access. So it is performed by the owner, on the owner's own account.

The clinics each have a marketplace persona now. They did not before: the catalogue seeder gives
each publishing organisation one member, `owner@<slug>.seed.local`, which is a synthetic session
user with no password — so the three clinics owned their cards and no human could act for them.

```bash
CLINIC_PASS_DIR=. node harness/clinic-admins.js            # sign up, then print the SQL
CLINIC_PASS_DIR=. node harness/clinic-admins.js --verify    # sign in as each, list its orgs
```

The script creates the users through the ordinary sign-up and then **prints** the one row per
clinic that makes each of them an `admin` of it. Printed rather than executed: inviting somebody
into an organisation requires already being an admin of it, the only existing member cannot sign
in, so there is no bootstrap path — and a privileged write on somebody else's database should be
read before it is run. Take a backup first; the one this was tested against is
`backup-20261124T223326Z-pre-clinic-admins.sql`.

Then the beat itself, with the researcher's deployment reading all three releases:

```bash
OWNER=harbor-point-admin@local.test PASS_FILE=./harbor-point-admin.password \
DATASET=harborpoint-release GRANTEE=big-pharma \
  node harness/withdraw-grant.js            # show the allow-lists
  node harness/withdraw-grant.js --revoke   # drop the grantee, record what was there
  node harness/withdraw-grant.js --restore  # put back exactly what was recorded
```

Measured on 2026-11-24, as `harbor-point-admin@local.test`:

| | |
|---|---|
| before | organisations `["aivo","big-pharma","harbor-point-gastro"]` |
| after `--revoke` | organisations `["aivo","harbor-point-gastro"]` |

and the researcher, asked the same question it had just answered:

| Network | screening with adequate prep | among them, with adenoma |
|---|---|---|
| Cypress Bay Digestive Health | 2,274 | 865 |
| Harbor Point Gastroenterology | **Cannot be read — access is denied** | **Cannot be read — access is denied** |
| Ridgeline Endoscopy Partners | 1,105 | 292 |

quoting the gate's own words back: *"Conversational Analyst is not allowed to read Harbor Point
Gastroenterology — Colonoscopy Quality Release (aggregate counts)"*. The other two networks are
untouched, which is the half of the beat that makes the point: one owner withdrew, and the
comparison carried on without them.

The clinic's own journal, which is where the owner sees it:

```
harborpoint-release | allowed | …
harborpoint-release | denied  | Conversational Analyst is not allowed to read Harbor Point …
harborpoint-release | allowed | …     ← after --restore
```

**Do not read a latency off those timestamps.** The ordering is right and the verdicts are right,
but the recorded times lag the actions by a minute or two in both directions — the marketplace
collects these events from the gatekeeper rather than being handed each one as it happens, and in
this run a `denied` was recorded after the restore had already been applied. What the journal
proves is that the refusal happened and why; how fast it happened is a different measurement and
this is not it.

After `--restore` the same question answers 1,438 / 665 again.

### What a tenant is given, and what it is not

```bash
APP_BASE=https://analyst-stand.conf-apps.superprotocol.dev \
PEOPLE='[{"label":"Clinic","user":"gate-rehearsal@local.test","passFile":"./stand.administratorPassword"},
         {"label":"Researcher","user":"research-network-admin@local.test","passFile":"./researcher.password"}]' \
PROBE_PROVIDERS='[{"label":"a clinic connection","id":"<provider id>"}]' \
  node harness/tenant-isolation.js
```

The questions this answers are about **what a tenant holds**, and that is the claim to make. On
2026-11-24:

- a tenant is listed only its own data sources. The installation administrator, who is in Cypress
  Bay, saw that tenant's sources; the researcher saw its own three and nothing else.
- a lookup **by id** of a source the tenant does not hold returns nothing, because the schema
  catalogue is tenant-scoped. Be exact about the shape of it: the query **succeeds and returns an
  empty list** rather than erroring. The wording should not imply a refusal where there is an empty
  answer.
- the model registry is refused outright — `Insufficient permissions` — because registering a model
  is an installation permission and an organisation administrator does not hold one.
- a tenant cannot read the **connection configuration** of a source, its own included:
  `adminDataSourceProvider` is refused with `Insufficient permissions`. That is the one question
  here about a secret rather than about a row, and `tenant-isolation.js` asks it without printing
  what it is asking after.
- a tenant cannot point itself at a connection belonging to another tenant. Reusing an existing
  provider through `adminRegisterDataSource` requires
  `installation.data-source-providers.write`, which an organisation administrator does not hold —
  which is why `researcher-sources.js` is something the **operator** runs for the researcher and
  not something the researcher could run for itself.

The demo-level version is to ask the researcher's own agent. Handed both the id and the SQL it
answers:

> The data source `cypressbay_digestive_health` was not found for your tenant
> (`01253f79-…`).

**What to say about the arrangement, and nothing beyond it.** The researcher runs on **its own
deployment, which mounts only the clinics' three aggregate releases** — counts, with no patient,
encounter or clinician in them. That is the arrangement to describe, because it makes the claim a
fact about what exists in a namespace rather than a claim about a boundary. Describe what each
party holds. Do not extend it into a statement about what a party could otherwise reach.

The researcher's user on the ANALYST stand's realm is **disabled** (2026-11-24), and should stay
that way: the researcher's seat is its own deployment, and two places to sign in as the same party
is one place too many to keep true.

### Putting a clinic corpus on the server, and its reader beside it

Before a card can be mounted it has to have data behind it and an account that can read exactly
that and nothing else. Both run **inside `marketplace-api-1`**: that container already holds the
vault key the storage credential is encrypted with, and it is one of the two hosts that
ClickHouse answers at all — the data host itself takes no SSH key from the agent host. So the
owner credential is decrypted and used where it already lives, and nothing prints it.

```bash
# on the marketplace host, with the corpus extracted to /tmp/corpus/<network>
ROW=$(docker exec marketplace-postgres-1 psql -U marketplace -d swarm_marketplace -At \
  -c 'select row_to_json(t) from (select host, port, username, "passwordCipher" from connected_storages limit 1) t')
KEY=$(sudo sed -n 's/.*encryptionKey: *"\(.*\)"/\1/p' /opt/marketplace/marketplace.yaml)

docker cp harness/load-corpus.js marketplace-api-1:/tmp/
docker exec -e VAULT_KEY="$KEY" -e STORAGE_ROW="$ROW" \
  -e CORPUS_DIR=/tmp/corpus/ridgeline -e TABLE_PREFIX=rep_ \
  -e READER_USER=rep_reader -e READER_PASSWORD="$PASSWORD" \
  marketplace-api-1 node /tmp/load-corpus.js
```

`load-corpus.js` applies the generated `schema.sql` one statement per request — ClickHouse's HTTP
interface runs exactly one, and a whole file answers 400, which reads like a malformed statement
and is not — then loads each TSV and **compares the row count in the table with the row count in
the file**, which is the only thing that distinguishes a load from a load that half worked. It
then creates the reader.

**The reader is created with `SETTINGS readonly = 2` on the account, never by attaching this
server's shared `readonly` profile, which is `readonly = 1`.** That distinction decides whether
the stand answers questions at all. The analyst's ClickHouse adapter sends `max_execution_time`
with every query and `max_result_rows` whenever a row limit applies, and `readonly = 1` refuses a
settings change as well as a write — so every question comes back `Code: 164` while the gate
still reports `allowed` and the card still looks connected. That cost SUP-905 a day.

`harness/check-readers.js` is what proves the isolation rather than asserting it:

```bash
READERS='[{"own":"hpg_","user":"hpg_reader","password":"…"},
          {"own":"rep_release_","user":"rep_release_reader","password":"…","tables":4}]'
docker exec -e VAULT_KEY="$KEY" -e STORAGE_ROW="$ROW" -e READERS="$READERS" \
  marketplace-api-1 node /tmp/check-readers.js
```

`tables` is how many tables that dataset has — eleven for a clinic corpus, which is the default,
and four for an aggregate release. A reader that can see one table more than its own dataset has is
the failure this check exists for, so the number is a parameter and not an assumption.

Per reader: it reads its own tables; it is refused **every other dataset on the server**, derived
from `system.tables` rather than from a list somebody has to remember to extend, and probed at a
table that really exists — a probe at one that does not answers `UNKNOWN_TABLE`, which proves
nothing about the grant; it sees only its own tables in `system.tables`; `INSERT` and
`DROP TABLE` are refused; and a query carrying `max_result_rows` succeeds, which is the
`readonly = 2` check and the one that would otherwise be found by a customer.

### Mounting a different set of dataset cards

Every other script here talks to the application. This one talks to the **marketplace**, because
what it changes is not configuration inside the deployment — it is which gated datasets the
deployment holds, and that is a deployment parameter.

```bash
MP_EMAIL=… MP_PASSWORD=… node harness/marketplace.js        # once; stores a bearer token, 0600

MP_ORG=<deploying org id> MP_DEPLOYMENT=<deployment id> \
  node harness/mount-datasets.js                            # dry run — the default

MP_ORG=… MP_DEPLOYMENT=… node harness/mount-datasets.js --verify    # the checks alone, read-only
MP_ORG=… MP_DEPLOYMENT=… node harness/mount-datasets.js --apply --i-have-a-go
MP_ORG=… MP_DEPLOYMENT=… node harness/mount-datasets.js --rollback --i-have-a-go

# on this cloud, which predates SUP-383, the window has to be held open twice —
# see "A window does not restart anything on a cloud without SUP-383"
MP_ORG=… MP_DEPLOYMENT=… node harness/mount-datasets.js --apply --pause --i-have-a-go
MP_ORG=… MP_DEPLOYMENT=… node harness/mount-datasets.js --resume --pause --i-have-a-go
MP_ORG=… MP_DEPLOYMENT=… node harness/mount-datasets.js --resume --i-have-a-go
```

`MP_DATASETS` is the comma-separated set to mount and it replaces what is there, so it names every
card the deployment should end up with, not the ones being added. The set as of 2026-11-24 is
`cypressbay-digestive-health,cypressbay-release,harborpoint-release,ridgeline-release`.

**This is the one script here that takes the stand off the air.** A gated dataset reaches a
deployment three ways at once and `reconfigure` writes all three — `datasetsJson`, from which the
gatekeeper derives the sources it admits that deployment to; the egress sidecar's nginx and
Service, one listener per mounted dataset; and the grounding Job, whose name is a hash of the
bindings. The last two are workloads, and a published cluster space refuses every workload
change, so the only path is `unpublish → reconfigure → publish` and it costs two pod bring-ups.
Plan it as a window. PVCs survive it — nothing in the teardown deletes a claim — so the semantic
model, the dashboards, the library and the autopilots in the deployment's own PostgreSQL come
back with it.

Three things the script will not get wrong, each of which has cost somebody a stand:

- **It carries no parameter literal.** `reconfigure` replaces the parameter set wholesale and the
  parameter set is attested, so a value that drifts moves the evidence digest — and a moved digest
  costs the deployment its access to every gated dataset it has, because the gate holds it to the
  digest its listing declares. The script reads the live deployment's own `parameters` back from
  the marketplace, sends exactly those, and writes them to a recovery file first. Sensitive
  parameters are absent rather than blank, which the API reads as "keep".
- **It checks the cards before anything goes down**, with the same two conditions
  `grants.isBindable` applies: the grant to the organisation that owns the *deployment*, and the
  grant to the application. A card that is not published, not gated, or not granted stops the run
  with the stand still up.
- **It verifies the digest after the publish** rather than assuming a dataset change leaves it
  alone, and it exits non-zero on either of two conditions: the cloud's measured digest no longer
  equals what the listing version declares, which is the comparison `clientsFor` makes and so is
  whether the gate admits the deployment at all; or the measured digest has moved from what it
  was before the window. Then every published hostname's own `/.well-known/swarm-evidence` is
  fetched and compared, because the marketplace reads the cloud's API while the gate reads the
  hostname. On any of them it says what is wrong and tells you to roll back rather than start
  debugging a stand that is down.

  **Two traps in that check, both of which make a healthy stand look broken.** Neither digest to
  compare against is `expectedEvidenceDigest` on the deployment: that field is `renderDigest`
  under its former name (`deployments.controller.ts:61`), computed from the manifests before
  deploying, and a render digest can never equal a collected one because a cluster defaults
  fields no template writes. On this stand they are `9e02e6f1…` and `d391cb85…` and always will
  be. And the two sources spell the same digest differently — the marketplace gives bare hex, the
  published bundle gives `sha256/<base64url>` — so both are normalised before comparison. A
  string comparison across that boundary is false every time.

`--verify` runs exactly those checks and nothing else, read-only, so the stand can be confirmed
healthy before a window as well as after one. With no recovery file it checks only that the gate
will admit the deployment; with one it also checks the digest against the recorded baseline.

**`--apply` refuses outright when the stand is already failing that check** rather than warning
and proceeding. A window run from there cannot be judged — the post-window assertion would find a
mismatch and blame the window for one it inherited — and the rollback it ordered would restore a
state the gate was refusing anyway. `--rollback` is exempt: a stand in that state is exactly when
you want to put it back.

When anything fails, the script prints the stand's actual state — `status`, `published`, what is
mounted — says plainly if it is off the air, and gives the rollback command, or says that nothing
was changed. A failure in the middle of a window leaves somebody reading that output with the
stand down, and a bare message does not tell them which of those two situations they are in.

The recovery file **exists if and only if a window has been started**: it is written on the line
before the unpublish and nowhere else. That ordering is the whole of its meaning. Writing it when
the baseline was merely read — which is the obvious place to put it — leaves a file behind
whenever a run is refused before it touches anything, and then the failure report tells you to
roll back a stand nothing happened to, the next `--apply` treats the file as an unfinished window
and reuses its baseline so a digest refusal becomes permanent until somebody deletes the file by
hand, and a refused card check says "nothing was touched" and is contradicted three lines later.

So: a dry run and every refused run leave no file. A file with no `completedAt` means a window
really did start and did not finish, and the next `--apply` reuses its baseline rather than
recording the half-finished state as the thing to go back to. `--rollback` refuses to run without
a file — restoring the state a stand is already in is not a rollback — and deliberately does not
rewrite one, because the file is the record of where to go back *to*.

### Did it start WORKING, as opposed to attesting correctly?

```bash
APP_BASE=… APP_USER=… PASS_FILE=… MP_ORG=… MP_DEPLOYMENT=… node harness/check-grounded.js
… MP_WAIT_MIN=30 node harness/check-grounded.js          # how long to allow (default 30)
… MP_HOST_SSH=user@host node harness/check-grounded.js   # also read the gate's verdicts
```

`mount-datasets.js` proves a window did not break the attestation. On the 2026-11-24 window
everything it checks was green — the digest did not move, it still matched what the listing
declares, the hostname served the bundle — while **the two cards that window had just mounted were
invisible to the agent**. Mounting a dataset only puts it in the bundle. What turns it into
something the agent can answer about is the grounding Job, and that Job is not collected into
evidence, has no status the cloud will report from outside a published space, and legitimately
takes a long time: `wait_for` gives the API 15 minutes before it gives up, and the pod then
restarts with up to 6 minutes of backoff. Twenty-two minutes of silence is indistinguishable from
failure unless you know that.

So this is the other half, and deliberately a separate script with a separate verdict. Three
facts, in the order they become true:

1. the agent's own API answers at all;
2. every mounted card is a registered data source under the id the grounding Job derives from the
   dataset name — **and the system message names it**, which is the independent second signal,
   because `ensure_prompt` rewrites that message from the bindings at the end of the run. One
   signal can lie; two agreeing is what told us the Job had not finished rather than that the
   source list was stale;
3. the gate has logged an `allowed` verdict for each mounted card since the window — the only
   thing that says data is really being served rather than merely registered. That log belongs to
   the dataset's owner and not to the deploying organisation, so there is no API call for it;
   give `MP_HOST_SSH` and it is read over ssh, or it reports itself unchecked and prints the query.

**A failure here is not a reason to roll back**, and the script says so in its own output.
Rollback is for an attestation failure — a digest that moved, a workload that is down — where the
stand is not serving what it claims to serve. A slow or failed grounding is diagnosed with the
stand up and the data it was already serving still flowing.

### Do not demonstrate during a window

For the twenty-five seconds or so that the space is unpublished, **the gate refuses the stand every
dataset it has**, and the refusal is logged against the clinic:

```
cypressbay-digestive-health | denied | this deployment is not currently allowed to read any data here
```

That is `clientsFor` doing its job rather than a fault: it builds the admitted-client document only
from deployments that are `published`, so an unpublished deployment is simply absent from it and the
gate has nothing to admit. A question asked in that gap comes back with no data, and a `denied` row
sits in the clinic's own access log afterwards — which is the one place a viewer might look and the
worst possible moment for them to look there.

So: no dialog, no autopilot run and no screen-share for the length of a window. The autopilots are on
a schedule, so a window that overlaps one leaves a `denied` behind with nobody at the keyboard.

### A window does not restart anything on a cloud without SUP-383

`azure-test` runs a build from before 2026-11-17 (its deployment evidence collects no `Role`,
`RoleBinding`, `ServiceAccount`, `DaemonSet`, `CronJob`, `HorizontalPodAutoscaler` or
`PodDisruptionBudget`, all of which SUP-385 and SUP-400 added that day). On such a build `unpublish`
withdraws attestation and releases hostnames and **stops no workloads** — every pod keeps running
across the whole window.

Two consequences, both of which cost SUP-909 a day:

- **The egress sidecar never reloads.** It mounts `nginx.conf` by `subPath`, so only a new pod reads
  a new config. Mount a dataset and the ConfigMap gains its `listen` line, the Service gains its
  port, and the running nginx keeps the listeners it had at startup — so the new dataset has no
  listener and the grounding Job's catalogue read cannot connect. The stand ran for 30 hours with
  one listener while its ConfigMap declared three.
- **A failed Job is never retried.** `BackoffLimitExceeded` is terminal, the old Job object is not
  deleted by the unpublish, and a reconfigure server-side-applies the same spec as a no-op — so
  further windows do nothing at all.

The fix, until that cloud is upgraded (**SUP-916**, and read it first — the upgrade moves the
evidence digest and would cost the stand its gated access), is to delete the two objects by hand
inside the window, which changes nothing that is collected. `mount-datasets.js` holds the window
open for it, in two places, because **the two moments are not interchangeable**:

```bash
# the namespace kubeconfig, from the cloud's own GraphQL as the space's owner
CLOUD_USER=… CLOUD_PASSWORD=… node harness/space-kubeconfig.js                   # list the spaces
CLOUD_USER=… CLOUD_PASSWORD=… SPACE_ID=… node harness/space-kubeconfig.js > ./stand.kubeconfig
export KUBECONFIG=./stand.kubeconfig

# 1. open the window: unpublish and stop
MP_ORG=… MP_DEPLOYMENT=… MP_DATASETS=… node harness/mount-datasets.js --apply --pause --i-have-a-go
kubectl get job
kubectl delete job <analyst-grounding-…>        # BEFORE the reconfigure: an unchanged spec is
                                                # re-applied as a no-op, so a Failed Job under the
                                                # same name would never be retried

# 2. reconfigure and stop again
MP_ORG=… MP_DEPLOYMENT=… MP_DATASETS=… node harness/mount-datasets.js --resume --pause --i-have-a-go
kubectl delete pod -l app.kubernetes.io/name=swarm-gatekeeper-egress    # AFTER it: only now does
kubectl exec <new pod> -- nginx -T | grep -E 'listen|proxy_pass'        # the ConfigMap declare the
                                                                        # new set
# 3. publish and check the digest
MP_ORG=… MP_DEPLOYMENT=… MP_DATASETS=… node harness/mount-datasets.js --resume --i-have-a-go
```

**The ordering is the whole point and it is easy to get backwards.** The sidecar mounts
`nginx.conf` by `subPath`, so only a new pod reads a new config — and the ConfigMap only gains the
new card's `listen` line at the reconfigure. Restart the pod before that and it comes back with
exactly the listeners it had, which is a window spent for nothing; after the publish, `delete` is
refused. There is one moment, and `--resume --pause` is it.

Pods and Jobs are not in the evidence snapshot and the ReplicaSet recreates the pod from an
unchanged spec, so the measured digest does not move — the 2026-11-24 window that mounted three
release cards and unmounted two identifying ones left `d391cb85…bf3a` exactly where it was. Verify
it rather than assuming it. Delete `./stand.kubeconfig` when the window closes: it is a credential
for a live namespace, and `.gitignore` only keeps it out of the repository.

**Expect the first grounding attempt to fail, and let it.** The gate's admitted-client document is
pushed on a timer, so for a minute or so after the publish the gate answers `denied — this
deployment is not currently allowed to read any data here`, the catalogue read fails terminally,
and `wait_for` spends its fifteen minutes polling a dead job. The pod then restarts under
`OnFailure` and the second attempt works: on 2026-11-24 the Job went `Complete` at the second
attempt, 4 sources, 4 tables each. One restart on a grounding Job is normal. Six were normal too —
what is NOT normal is `BackoffLimitExceeded`.

The one check none of this can do for you is the gate's own verdict: the access log belongs to the
dataset's owner, not to the deploying organisation. The script prints the SQL to run against the
marketplace's PostgreSQL, and an `allowed` row there is the only thing that says a clinic is
actually being served.

## Walking a viewer through it in fifteen minutes

1. **Signals** — two cards, `SURV-01`, P1, Risk. One says `×2`. Read the area line: a site and a
   named patient. *This is the task that cannot be done on de-identified data.*
2. Open the Ivey card — the card the model wrote, Evidence, Scope, History, `Repeats: 2 runs`,
   and **Open run**.
3. **Open run** → the autopilot run that published it, with the SQL it wrote and the rows the gate
   returned.
4. **Autopilots → Book the recall** → the run picker: `Event: Signal created`, `Manual`,
   `Manual`. One of them is the refusal.
5. **Administration → MCP servers** → one server, one tool, a token, and the sentence about
   outbound traffic.
6. The clinic's side, on its own screen: open `https://cypress-bay-scheduling.demo.superprotocol.dev`
   and sign in with the **practice's own staff token**. **Appointments** — the recalls that were
   booked, each stamped with the evidence digest the call was accepted from; **Journal** — the
   calls that reached the attestation check, with their verdict and the refusals in red beside the
   acceptances; the credential-less probes anyone has ever aimed at the hostname are one click
   away behind *Show all*, not dropped. (The same two things are `GET /appointments` and
   `GET /journal` as JSON, for anyone who prefers a `curl`.) The deployment's token opens none of it: it carries `scopes: ["mcp"]` and these want
   `read`, which is what keeps the one tool it was given from being a way back into the chart —
   paste the deployment's token into the sign-in form and watch it refused.

   The Journal page states its own limits where a viewer reads them: the check binds the approved
   code to the **hostname**, the **token** binds the individual call, the code is re-checked once
   per 60-second window, and the deployment's outbound destinations are not all attested yet.
7. **Chat** → attach `tillerson-nadine-report-2025-10-05.pdf` and ask it to reconcile. It reads a
   named patient's signed report and catches an error in the record.

Opening a card marks it **Seen** and moves it from `new` to `acknowledged`, so show the feed
before opening anything.
