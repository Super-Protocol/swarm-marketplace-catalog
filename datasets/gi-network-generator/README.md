# The GI clinic network generator

One program, three published datasets. It produces a synthetic US gastroenterology network —
patients with names, colonoscopies, polyp-level findings, histopathology, post-polypectomy
surveillance intervals computed from a guideline, the recall each interval generates, every
outreach attempt against it, and a register of clinical documents with the reports themselves in
PDF, DOCX and plain text.

| | Dataset | |
|---|---|---|
| `cypress-bay` | `datasets/cypressbay-digestive-health` | six sites in Florida, 12,000 patients, record from 2019, complete capture |
| `harbor-point` | `datasets/harborpoint-gastroenterology` | two academic sites in Massachusetts, 3,400 patients, record from 2022, complete capture, the highest detection rate |
| `ridgeline` | `datasets/ridgeline-endoscopy` | eleven rural sites in Colorado, 18,000 patients, record from 2016, **incomplete capture**, a feed two months behind |

This directory is not a dataset. It holds no `data.yaml` and is not listed in `catalog.yaml`;
it is the shared code the three cards' corpora are built from, in the same spirit as
`apps/tests/`.

## Why one program

The clinical model is the same for all three: the same specialty, the same country, the same
guideline, the same anatomy, the same set of things an outreach attempt can end in. Three copies
of it would be three places for a rule to drift, and the drift would land in exactly the figure
the demonstration turns on — a cross-network comparison is only meaningful if the networks were
measured by the same ruler.

What differs is the practice, and that lives in `profiles.py`: sites, volumes, periods, payers,
laboratories, how well the examinations go, and how completely the forms get filled in. Read
that file before changing anything here; the reasoning behind each knob is in it.

## Running it

```bash
./generate.py --network ridgeline --out ./out
./generate.py --network ridgeline --out ./out --no-documents     # tables only
./generate.py --network ridgeline --out ./out --patients 2000    # a smaller run
```

Each dataset directory also carries a `generate/generate.py` with its own network already
chosen, so the documented command for a given corpus stays in that corpus's directory:

```bash
cd ../ridgeline-endoscopy/generate && ./generate.py --out ./out
```

`--seed`, `--patients` and `--table-prefix` default to the chosen network's own, so a published
corpus is reproduced by naming the network and nothing else. Overriding any of them produces a
corpus that is *not* the one the card declares — fine for a quick look, never for a reload of
the stand.

`out/` gets the TSV tables, a `schema.sql`, a `load.sh`, the documents under `notes/`, and a
`MANIFEST.md`: the operator's crib sheet, which names the planted contradicting documents, says
what the right answer is for each, and — for a network with incomplete capture — states the
rates. None of it is committed, because every number in it is a property of the seed.

## The aggregate release

Each network publishes two cards. One is its record — identifying, granted to the operator it
chose. The other is a release of **counts**, granted separately, and it is what an outside
researcher is given:

```bash
./generate.py  --network ridgeline --out ./out/ridgeline
./aggregate.py --network ridgeline --corpus ./out/ridgeline --out ./out/ridgeline-release
```

Four tables, at network × site × month × procedure type: examinations performed, how they were
captured, bowel preparation including **what was never scored**, adenoma and advanced-histology
detection, cecal intubation, withdrawal time, surveillance intervals recorded, and the state of
every recall that fell due. Roughly a thirtieth the size of the corpus it counts.

There is no patient, no encounter, no medical record number, no procedure identifier, no free
text out of a chart — and **no clinician**. A per-endoscopist detection rate is the most
sensitive figure this corpus could yield and no question the release exists to answer needs it.
`aggregate.assert_no_record` checks that against the corpus rather than against this paragraph:
it collects every value in the source that identifies a patient, a specimen, a procedure or a
clinician and fails if any of them appears anywhere in the release.

Why a second card and not a view: a deployment grounds every card it mounts into one tenant, so
mounting an identifying card beside an outside researcher's work makes it readable by whichever
tenant that happens to be. What holds is not who may query a source but what is *in* it — a
release containing no record cannot be made to yield one.

Small cells are **not** suppressed: a single examination in one month at one site appears as a
count of one. A real release would round or suppress it. This one does not, because the property
the self-test checks is that the researcher's pooled figures come out *exactly* equal to the
figures computed from the records, and suppression would make them only approximately equal.

## Checking it

```bash
./selftest.py                                  # cypress-bay, small and fast
./selftest.py --network ridgeline
./selftest.py --all-networks                   # what a review wants
./selftest.py --network ridgeline --published-size --seeds 8   # what CI runs
```

Run `--all-networks`, not just the network you touched. The incomplete-capture knobs are
guarded on being non-zero — a network with complete capture draws no random number for them,
which is what keeps the already-published Cypress Bay corpus byte for byte what it was — so the
three take genuinely different paths through the generator and one network's pass says nothing
about the others.

`selftest.py` reads each network's own `data.yaml` — both of them, the identifying card and the
release card — and fails if either does not declare exactly the tables and columns its schema
creates. That check is the reason this runs in CI: a column renamed here and not in the card does
not fail a render or a lint, it silently teaches the consuming agent something false. It matters
more for the release card than for the other one, because the researcher who reads it holds no
identifying card to fall back on.

At `--published-size` the self-test also asserts the exact pooled adenoma-detection table the
stand answered on 2026-11-24 — 1825/4788, 1396/3052, 664/2365 with 1,587 excluded — computed from
the release's counts and again from the records, and requires the two to be equal and to equal
those figures. If a change here moves them, the stand's published numbers moved with it.

## The one rule that is easy to break

**A corpus that has been published must not move.** `cypress-bay` is loaded on a live
demonstration stand and carries a semantic model, two dashboards and fifteen library items that
somebody built by hand on top of it; its row counts are declared on its card. Changing the order
of a single `rng` call in a shared code path changes every row of all three corpora. So:

- when adding a knob, guard its draw on being non-zero (`profile.rate and rng.random() < …`),
  so the networks that do not use it consume no random numbers;
- after any change here, regenerate `cypress-bay` and diff it against a copy taken before —
  byte for byte, tables and documents alike. The only file allowed to differ is `schema.sql`,
  and only if this generator's own path appears in its header comment.
