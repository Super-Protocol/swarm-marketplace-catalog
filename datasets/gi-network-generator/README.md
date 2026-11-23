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

`selftest.py` reads each network's own `data.yaml` and fails if the card does not declare exactly
the tables and columns the schema creates. That check is the reason this runs in CI: a column
renamed here and not in the card does not fail a render or a lint, it silently teaches the
consuming agent something false.

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
