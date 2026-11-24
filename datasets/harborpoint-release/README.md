# Harbor Point Gastroenterology — Colonoscopy Quality Release (aggregate counts)

The counts Harbor Point is willing to hand to somebody outside it. The practice's records are
published separately, under `harborpoint-gastroenterology`, identifying and granted only to the
operator it chose.

Built by `datasets/gi-network-generator/aggregate.py` (`--network harbor-point`) from the corpus
that card's generator writes. 1,708 rows against the record's 45,719.

The shared material — why a release rather than a view on the records, what the four tables hold,
which two columns earn their existence, what is deliberately absent, why small cells are not
suppressed, and why counts are still read through the gate — is written once in
`datasets/cypressbay-release/README.md` and applies here unchanged with `cbdh_release_` read as
`hpg_release_`.

## What is different about this one

The period is the caveat. Harbor Point is the small, new and completely captured network: two
academic sites in Massachusetts, three and a half thousand patients, and a record that begins in
**June 2022** because the practice does. Nothing inside that period is missing — every
examination carries its preparation score and its withdrawal time, and every surveillance
interval reached the structured record, so `prep_missing` is zero throughout and no denominator
here has a hole in it.

What that makes easy to get wrong is a comparison that reaches further back. Before June 2022
this network has no record rather than a thin one, and a pooled trend starting in 2019 will show
Harbor Point appearing out of nowhere. `network_profile.first_procedure_date` is published on the
same row as the counts for exactly that reason, and `coverage_note` says it in words.
