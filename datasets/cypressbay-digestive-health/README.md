# Cypress Bay Digestive Health — Colonoscopy Surveillance (US)

A synthetic six-site US gastroenterology network: who the patients are, what was found at
colonoscopy, what the pathologist said, what interval that earned, and whether anybody ever got
the patient back in.

One of **three** identifying GI clinic cards on this catalogue, each owned and granted by a
different practice: this one, `harborpoint-gastroenterology` (two academic sites, small, recent,
completely captured) and `ridgeline-endoscopy` (eleven rural sites, a ten-year record with
declared gaps). They share a generator and a schema, which is what makes a question that crosses
them answerable, and they share nothing else — volumes, periods, detection rates and how
completely the forms are filled in all differ on purpose. See
`datasets/gi-network-generator/README.md`.

Everything here is generated. No row, no name and no sentence is taken from a real patient, a
real practice or a real dataset, or derived from one. The generator is deterministic — the same
seed reproduces the corpus byte for byte — so a regenerated copy is the same data, not similar
data, and the whole thing can be thrown away and rebuilt in under three seconds.

## Two shapes, and why both

A clinic holds two kinds of record and a tool that only reads one of them is not reading the
chart.

**Structured** — eleven ClickHouse tables, ~131,000 rows at the default size. Aggregates over
this are meaningful: an adenoma detection rate of 38.8%, 1,511 surveillance recalls genuinely
past due (1,149 of them by more than a year), a per-endoscopist withdrawal time.

**Free text** — 44 clinical documents in PDF, DOCX and plain text. Each one names a patient, a
date of birth and a medical record number, because that is what a clinical document does. They
are not in the database and they are not meant to be: they are uploaded to the application and
read whole, which is the path chart abstraction actually takes.

The two are deliberately **not** redundant. The point of the pairing is in the gaps:

| Gap | Where it is | What it is for |
|---|---|---|
| A colonoscopy performed at another facility has a `cbdh_procedures` row with a date, a type and an indication — and no findings, no pathology, no surveillance recommendation anywhere | `report_source = 'Outside report'`; the detail is in an 'Outside records packet' document | Chart abstraction. Every field the model is asked to extract is in the prose and in no column. |
| Five signed reports disagree with the record that was keyed in from them | the document; nothing in SQL says the row is suspect | Catching a transcription error that changed a surveillance interval. |
| A recall records *that* outreach failed and with which outcome code, never *why* | the coordinator's note | Deciding what to do next: a different preparation, a caregiver's number, or closing the recall. |

## Identifying, on purpose

The two clinical cards beside this one (`roczen-metabolic-programme`,
`autonomyx-inflammation-monitoring`) are de-identified: banded ages, synthetic subject keys, no
narrative. This one is the opposite, and the difference is the point.

A colonoscopy surveillance tool has to know *which* patient is overdue, write the recall against
*that* patient's record, and read a scanned report that names them. De-identifying the input
removes exactly the information the work needs. So `cbdh_patients` carries names, dates of birth,
medical record numbers, addresses and telephone numbers, and so does every document.

Which is why `access.mode: gateway` on this card is not a formality. A consumer never receives
the connection; an application is handed an address it can only reach the data through, and
every request is admitted against the deployment evidence that application published. The gate
is the thing standing between a named patient and software nobody approved.

## Contents

| Table | One row per | Notes |
|-------|-------------|-------|
| `cbdh_sites` | Clinical site | Six, all in Florida, with suite counts and opening dates. |
| `cbdh_providers` | Clinician or coordinator | Endoscopists, pathologists, surveillance coordinators, abstractors. NPIs all begin with 9, so none can collide with an issued number. |
| `cbdh_patients` | Patient | Identifying: name, date of birth, MRN, address, telephone, payer, preferred language, status. |
| `cbdh_encounters` | Office or telehealth visit | Pre-procedure, results review, and the new-patient visit at which outside records were requested. |
| `cbdh_procedures` | Endoscopic procedure | Colonoscopy, sigmoidoscopy, EGD. `report_source` decides whether the rest of the record exists; `procedure_type` decides whether there is a colon to make a statement about. |
| `cbdh_findings` | Polyp or lesion | Segment, size, Paris morphology, resection method, piecemeal flag. |
| `cbdh_pathology` | Specimen | Histology, dysplasia grade, margin, lab turnaround, and the `adenoma` / `advanced_histology` flags every rule turns on. |
| `cbdh_surveillance_recommendations` | Recommendation | Interval, due date, risk tier, and `basis` — the rule in words, so it can be checked. |
| `cbdh_recalls` | Recommendation | Status, days overdue, outreach count, and the examination that closed it. |
| `cbdh_recall_outreach` | Outreach attempt | Channel and outcome code. No narrative — that is the point. |
| `cbdh_clinical_documents` | Document held | Which file, where from, whether it has arrived, and whether anyone has read it. |

## Semantics

Every column declares a `role`: `measure` for the numbers worth aggregating, `dimension` for
the keys, categories and dates worth grouping by. Foreign keys are declared with `references`.

For this card that publication is not metadata, it is the product. A consuming agent builds its
entire knowledge of this database from the table and column descriptions in `data.yaml` — there
is nothing else to fill in and no schema document it can read instead. A column described
loosely is a column the agent will use wrongly, and the only place to fix it is the card.

Three descriptions in particular earn their length, because each one is a trap an agent falls
into on its own:

- **`cbdh_procedures.report_source`** — an adenoma detection rate computed over the whole table is
  diluted by procedures whose findings were never entered. The description says so.
- **`cbdh_recalls.days_overdue`** — zero on every status except `overdue` and `lost_to_follow_up`, so
  averaging it over the table understates it by an order of magnitude.
- **`cbdh_surveillance_recommendations.superseded_by`** — only the recommendation with this empty can
  be overdue. Without that, every historical recommendation on a patient with a long history
  looks past due.
- **`cbdh_recalls.recall_status`** — a recall is only *live* when it is `not_yet_due`,
  `due_soon`, `scheduled`, `overdue` or `lost_to_follow_up`. The rest are closed, and a document
  that proves the record wrong about a closed recall proves nothing: "the right answer is a
  repeat within three months" is not an answer about a patient who has died. Every planted
  document therefore sits on a live recall and an active patient.
- **`cbdh_procedures.cecal_intubation`** — null, not zero, for a flexible sigmoidoscopy. A
  sigmoidoscopy examines the left colon and stops by design; counting it as a colonoscopy that
  failed to reach the cecum both wrecks the cecal intubation rate and, if it is allowed to
  generate a "repeat within 3 months", fills the overdue cohort with patients nothing is wrong
  with. It did, in the first draft of this generator: 584 of them.

## Clinical content

The surveillance intervals are **computed, not sampled**. Every interval in the corpus follows
one guideline function (`generate/guidelines.py`, a reasonable summary of the recommendations in
general clinical use for post-polypectomy surveillance), so the whole dataset is internally
consistent. That matters: if intervals were random, every record would disagree with the
guideline and an assistant asked "is this patient's interval right?" would be correct to say no
about all of them, which proves nothing. Here the five planted disagreements are the only
disagreements, and finding one is a real finding.

Nothing here is clinical advice, and the guideline function is a demonstration artefact rather
than a certified implementation.

The anatomy is consistent too, which matters for the same reason. A colonoscopy that was
abandoned at the splenic flexure has no polyp recorded beyond the descending colon, in the tables
and in the prose; a sigmoidoscopy has none past the descending colon at all. A polyp reported in
a part of the colon the scope never entered is the first thing a gastroenterologist notices, and
the point at which they stop believing the rest.

## Generating and loading

The generator is shared between the three clinic networks and lives in
`datasets/gi-network-generator/`. `generate/generate.py` here is the documented path to it with
this network already chosen, so the command below is the one to run for this corpus:

```bash
cd generate
./generate.py --out ./out                   # 12,000 patients, ~131,000 rows, 44 documents
./generate.py --out ./out --patients 3000   # smaller
```

The self-test is shared too:

```bash
cd ../../gi-network-generator
./selftest.py                                                      # this network, small and fast
./selftest.py --network cypress-bay --published-size --seeds 8     # what CI runs
./selftest.py --all-networks                                       # all three — run this after any change
```

`selftest.py` is what `.github/workflows/dataset-definitions.yml` runs on any change under
`datasets/`, once per network. It needs PyYAML to read the card and `pdftotext` to confirm a
generated PDF still extracts as text with its identifiers intact.

**This corpus is loaded on a live stand and must not move.** A semantic model, two dashboards
and fifteen library items were built by hand on top of it, and the row counts above are on the
card. Any change to the shared generator has to be followed by a regeneration of this network
and a byte-level diff against a copy taken beforehand — see the last section of the generator's
README for why and how.

`--seeds` matters more than it looks. Every defect this generator has shipped was
**placement-dependent** — a contradiction planted on a recommendation a later colonoscopy had
already answered, a coordinator note about somebody's mother attached to a man, the showcase
document on a patient who had died — and each one was invisible under the single seed that was
run and present under about half the others. Without the `_eligible` guards, 7 of 16 seeds put a
planted contradiction on a patient the practice could no longer act on. One seed does not vouch
for a generator, so CI re-runs the placement assertions over eight.

`out/` gets the TSV tables, a `schema.sql`, a `load.sh`, the documents in `notes/`, and a
`MANIFEST.md` — the operator's crib sheet, which names the five contradicting documents and says
what the right answer is for each. `MANIFEST.md` is generated rather than committed because
every number in it is a property of the seed.

```bash
cd out
CH_URL=http://10.10.1.11:8123 CH_USER=<owner> CH_PASSWORD=<secret> ./load.sh
```

The schema drops and recreates every table, so a reload replaces the corpus rather than doubling
it — which is what you want on a stand that gets shown repeatedly. The documents in `notes/` are
uploaded to the application by hand; they are files a clinician reads, not a table, and that is
the only reason the abstraction scenario proves anything.

## Where the tables live, and why they are prefixed

The eleven tables are `cbdh_patients`, `cbdh_procedures` and so on, inside an existing
`marketplace` database rather than one of their own. That is not a style choice: on the
marketplace stand the ClickHouse account the marketplace holds carries
`CREATE DATABASE, CREATE TABLE ON marketplace.*` and nothing wider, so a `cypressbay_gi`
database cannot be created without host access to that server — and the clinic datasets already
published there (`roczen_*`, `autonomyx_*`, `reset_*`) live the same way.

The prefix is therefore part of the published names and the card declares them that way. The
reader account behind the card's secrets is granted `SELECT` on these eleven tables and nothing
else in the database, so sharing the database shares no data.

The prefix is also what lets the three clinic corpora share one database without sharing a
table or a reader account: `cbdh_*`, `hpg_*` and `rep_*` sit side by side in `marketplace`, and
each card's reader is granted `SELECT` on its own eleven and nothing else. Three networks in one
database is a property of this stand, not of the data — and it is invisible to a consumer, which
reaches each one through its own gate port under its own grant.

On a ClickHouse you own, `--database cypressbay_gi --table-prefix ""` gives clean names; change
the card's `database` and its table names to match before publishing from there.

## Access

Restricted. Synthetic demonstration data: it may be loaded, queried and shown freely, and it
carries no patient confidentiality obligation because there is no patient. It is not a
substitute for real data in any evaluation that claims clinical validity.

## Endpoint

Served over the ClickHouse HTTP interface, behind the gate. The account behind the card's
publisher secrets is read-only and confined to this database; write statements are refused by the
server, not by convention.

**That account must be `readonly = 2`, not `readonly = 1`.** The two differ in one thing:
`readonly = 1` refuses a SETTINGS CHANGE as well as a write, and a client that sends per-query
limits is a client that changes settings. Conversational Analyst's ClickHouse adapter sends
`max_execution_time` with every query and `max_result_rows` + `result_overflow_mode` whenever a
row limit applies — the result policy always sets one — so under `readonly = 1` every question
the agent asks of this dataset comes back

```
Code: 164. DB::Exception: Cannot modify 'max_result_rows' setting in readonly mode. (READONLY)
```

and the model, having no rows, explains at length that it cannot answer. Nothing else shows it:
the gate still reports `allowed`, the schema catalogue still builds, the card still looks
connected. `readonly = 2` is ClickHouse's own "read-only queries, settings may be changed" —
`INSERT`, `CREATE`, `ALTER` and `TRUNCATE` stay refused, and the account's grants are
`SELECT`-only on these eleven tables in any case. Found on the Latica stand on 2026-11-23
(SUP-905), where it had been invisible because no dialog had ever run.
