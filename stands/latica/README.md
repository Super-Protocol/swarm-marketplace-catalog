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
```

`PASS_FILE` and `TOKEN_FILE` are paths, never arguments: a credential must not reach a shell
history or a process list. Nothing in this directory contains one, and `stands/latica/.gitignore`
is written so that nothing a run produces can become one by accident — the token files the
commands above name, the `*-runs.json` / `*-signals.json` / `autopilot-*.id` the harness writes
beside itself, the screenshots, the generated corpus and the receiver's own state are all
ignored.

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
READERS='[{"own":"hpg_","user":"hpg_reader","password":"…"},{"own":"rep_","user":"rep_reader","password":"…"}]'
docker exec -e VAULT_KEY="$KEY" -e STORAGE_ROW="$ROW" -e READERS="$READERS" \
  marketplace-api-1 node /tmp/check-readers.js
```

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

MP_ORG=… MP_DEPLOYMENT=… node harness/mount-datasets.js --apply --i-have-a-go
MP_ORG=… MP_DEPLOYMENT=… node harness/mount-datasets.js --rollback --i-have-a-go
```

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
  alone — against both the marketplace's copy and the live `/.well-known/swarm-evidence` a
  consumer reads. If it moved, it says so and tells you to roll back instead of debugging a stand
  that is down.

The one check it cannot do for you is the gate's own verdict: the access log belongs to the
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
