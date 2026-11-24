# Cypress Bay Digestive Health — Colonoscopy Quality Release (aggregate counts)

The counts Cypress Bay is willing to hand to somebody outside it. The practice's records are
published separately, under `cypressbay-digestive-health`, identifying and granted only to the
operator it chose. This card is a second, separate decision with a second, separate grant, and
what it carries is arithmetic.

Built by `datasets/gi-network-generator/aggregate.py` from the corpus that card's generator
writes. Synthetic throughout, deterministic, and roughly a thirtieth the size of the record it
counts: 7,308 rows against 131,126.

It is one of three releases built the same way — `harborpoint-release` and `ridgeline-release`
are the other two. The shared material is written here once and applies to all three.

## Why a release and not a view

A deployment grounds **every** card it mounts into **one** tenant: the deployment's
`primaryOrgAlias`. So what a deployment mounts is a decision about that tenant, and the way to
give two parties different data is to give them different deployments.

**A release contains no record, so it cannot yield one, whoever queries it and with whatever
tool.** That is the reason these cards exist, and the reason a researcher holds releases and
nothing else. It is a property of the data rather than of a configuration: nothing has to be set
correctly for it to stay true, and it does not depend on anybody anticipating the question.

## What is in it

Four tables. One row per network (`*_network_profile`), one per site (`*_sites`), and two fact
tables at **network × site × month**:

| | |
|---|---|
| `*_procedure_monthly` | by procedure type: examinations performed, how they were captured, bowel preparation adequate / inadequate / **missing** / described in prose, adenoma and advanced-histology detection, cecal intubation, withdrawal time, surveillance intervals eligible and recorded |
| `*_recall_monthly` | by risk tier and status: recalls that fell due, days overdue, outreach attempts |

Two columns earn their existence over the obvious shape:

- **`prep_adequate_with_adenoma`** is published as its own count because it cannot be recovered
  from `with_adenoma` and `prep_adequate` side by side — the two sets overlap in a way only the
  record knows. Without it, an adenoma detection rate over adequately-prepared examinations is
  not computable from counts at all, and the whole release would fail at the one question it
  exists for.
- **`prep_missing`** is the size of the exclusion. A rate that quietly drops the examinations with
  no preparation score is wrong in a way nobody downstream can see; a rate published beside the
  count of what it dropped is a rate a reader can argue with.

## What is deliberately not in it

No patient, no encounter, no medical record number, no name, no date of birth, no procedure
identifier, no free text out of a chart — and **no clinician**. A per-endoscopist detection rate
is the most sensitive figure this corpus could produce, it identifies a named clinician in a
network with a handful of endoscopists, and no question the release exists to answer needs it. A release has to decide what it
is not for, and that is the decision.

`aggregate.assert_no_record` checks this against the corpus rather than against this paragraph:
it collects every value in the source that identifies a patient, a specimen, a procedure or a
clinician, and fails if any of them appears anywhere in the release. `selftest.py` exercises it
both ways — a clean release passes, and a release with a medical record number planted in it
fails.

## Small cells are not suppressed

A single examination in one month at one site appears as a count of one. A real release would
round or suppress that, and this one does not — because the property the self-test checks is that
a researcher holding only these counts reproduces the practice's own figures **exactly**, and
suppression would make them only approximately equal. Said here rather than left for somebody to
discover.

## Still behind the gate

`access.mode: gateway`, like the identifying card. These are counts, not records, but they are
still the practice's to withdraw: the grant behind this card can be revoked, and when it is, the
gate stops admitting the deployment for this card within about half a minute and the access
journal shows where the data stopped. That is why a release is a card and not a spreadsheet
attached to an email.

## Checking it

```bash
cd ../gi-network-generator
./selftest.py --all-networks --published-size
```

At the published size the self-test asserts the exact pooled adenoma-detection table the Latica
stand answered on 2026-11-24 — Cypress Bay 1,825/4,788, Harbor Point 1,396/3,052, Ridgeline
664/2,365 with 1,587 excluded — computed from these counts and again from the records, and
requires the two to agree and to equal those figures.
