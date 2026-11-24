# Ridgeline Endoscopy Partners — Colonoscopy Quality Release (aggregate counts)

The counts Ridgeline is willing to hand to somebody outside it. The practice's records are
published separately, under `ridgeline-endoscopy`, identifying and granted only to the operator
it chose.

Built by `datasets/gi-network-generator/aggregate.py` (`--network ridgeline`) from the corpus that
card's generator writes. 12,415 rows against the record's 145,037.

The shared material — why a release rather than a view on the records, what the four tables hold,
which two columns earn their existence, what is deliberately absent, why small cells are not
suppressed, and why counts are still read through the gate — is written once in
`datasets/cypressbay-release/README.md` and applies here unchanged with `cbdh_release_` read as
`rep_release_`.

## What is different about this one

The gaps, and they are the point. Ridgeline is the large, old and **incompletely captured**
network: eleven sites across Colorado, eighteen thousand patients, a record going back to March
2016, and real holes in it. About a third of its structured examinations carry no
bowel-preparation score, a fifth describe the preparation in prose instead of scoring it, nearly
half carry no withdrawal time, a fifth of surveillance intervals never reached the structured
record — so those patients generate no recall at all — and the feed runs about two months behind.

Every one of those is published here **as a count**: `prep_missing`, `prep_free_text`,
`withdrawal_recorded` beside `procedures`, `surveillance_eligible` beside `interval_recorded`,
and `feed_lag_days` and `data_through` on the network row. So a rate computed from this release
can state its own exclusions. On the stand, an analyst asked for a pooled adenoma detection rate
volunteered that Ridgeline had excluded 1,587 examinations — 33.4% of its colonoscopies — for
missing preparation scores, without being asked to check. That is the behaviour the gaps exist to
produce, and it only works because the exclusion is a number in the data rather than a remark in
somebody's documentation.

`catalog.yaml` scores this release's usability **higher** than the identifying card's 7.4, which
is not a contradiction: the gaps are the same gaps, but here each one is measured. A measured gap
is more usable than an unmeasured one.
