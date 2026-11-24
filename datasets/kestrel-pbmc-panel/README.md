# Kestrel PBMC Immune Panel

A synthetic single-cell corpus: a 933-gene targeted immune panel measured across 1,800
peripheral blood mononuclear cells, four preparation runs, twelve donors and two sites, with
nine annotated populations.

Everything here is synthetic. No cell, donor or measurement comes from a real experiment and
nothing is derived from one — it is a demonstration corpus whose job is to make a **gated read**,
and the clustering that follows it, something a reader can check.

The **genes are real** and the **counts are not**, which is a deliberate split rather than a
half-measure. The ported notebooks flag mitochondrial genes by the `MT-` prefix and ribosomal
ones by `RPS`, and `02_regulatory_activity.ipynb` scores transcription-factor activity against
CollecTRI, which is indexed by HGNC symbol. A panel of invented symbols would run and score
nothing while looking exactly like it had worked, which is the worst outcome available. So
`generate/panel.py` is a curated list of real symbols — and curating is what makes this a
**targeted panel** of about a thousand genes rather than a whole transcriptome.

```
datasets/kestrel-pbmc-panel/generate/generate.py --self-test
datasets/kestrel-pbmc-panel/generate/generate.py --out-dir /tmp/kestrel
```

Deterministic — one seed, one matrix, byte for byte — so a regenerated copy is the same data
rather than similar data:

| | rows | sha256 of the TabSeparated output |
|---|---|---|
| `sc_cells` | 1,800 | `33d23f35c277a8df9dddf6543b4928f00f227bc14e83835c8b246bf148a188a5` |
| `sc_genes` | 933 | `29297cc83ce087e12d150b9acdf50a73d53ae9688443793de79fa0ded069a416` |
| `sc_counts` | 1,101,372 | `371f1fab57d310ae0e0452aad648132c2b4c290c7017b0086d868a1f799a71a7` |

`--self-test` **asserts** those numbers rather than printing them, along with referential
integrity between the three tables, that `data.yaml` declares exactly the columns the generator
writes, that the panel still carries an `MT-` prefix, an `RPS` prefix and `GATA3` by name, and
the QC property below. It runs in CI on every change under `datasets/`
(`.github/workflows/dataset-definitions.yml`), because a column renamed here and not in the card
fails nothing — it quietly teaches a consumer something false.

## Why it is three ClickHouse tables and not an `.h5ad` in a bucket

Because an `.h5ad` in a bucket **cannot be gated**. The marketplace gatekeeper is an HTTP proxy
in front of ClickHouse: it injects ClickHouse credentials into every request it forwards and
rewrites the database the caller named, and it carries no other protocol. So a matrix whose owner
wants to approve a specific build before it is read has to be ClickHouse tables, in the long form
`sc_workspace._read_matrix_from_clickhouse` assembles:

| table | becomes |
|---|---|
| `sc_cells` | `adata.obs`, indexed by `cell_id` |
| `sc_genes` | `adata.var`, and the gene order of the assembled matrix |
| `sc_counts` | `adata.X`, as a CSR matrix of the non-zero entries |

[Confidential Single Cell Analysis](../../apps/confidential-single-cell-analysis/) accepts
`s3-h5ad/v1` as well, and prefers this interface over it for exactly this reason. Until this
dataset existed, that listing's confidentiality claim could not be demonstrated at all — there
was no gated matrix to point it at (SUP-900).

## What is in it

Nine populations, separable on their own programmes, because a demonstration matrix whose
clusters cannot be named is one nobody can check:

| population | cells | population | cells |
|---|---|---|---|
| CD4 T | 540 | CD14 Mono | 371 |
| CD8 T | 301 | CD16 Mono | 91 |
| NK | 177 | cDC | 43 |
| B | 174 | Platelet | 79 |
| Plasma | 24 | | |

Three things in it are there to give a step in the ported workflow something real to do:

- **A per-run, per-gene capture effect.** `batch` (`run_a` … `run_d`) carries a multiplicative
  shift, so Harmony integration in `01_preprocessing.ipynb` has something to correct. Without
  one, the notebook's before-and-after embeddings are the same picture twice.
- **Degraded cells.** About one in twenty-five has a small library and a high mitochondrial
  fraction; median `pct_counts_MT` is 5.4% and **56 cells** are above the notebook's
  `pct_counts_MT < 20` cut. A QC step that removes nothing is a QC step nobody can tell works.
- **An interferon-response module in three donors.** `D03`, `D07` and `D11` were "sampled during
  an acute viral episode", so the ISG genes rise across every cell type — a donor-level effect
  that shows up when a UMAP is coloured by donor and not when it is coloured by cell type.

`batch` and `cell_type` are the columns the listing's two data parameters default to, so a
deployment reading this dataset answers **neither** of them.

### One consequence of the panel being targeted

`01_preprocessing.ipynb` and `03_pearson_residuals.ipynb` both ask for `n_top_genes=5000` highly
variable genes, and this panel has 933. Scanpy's own behaviour is to log that `n_top_genes` is
greater than `n_vars` and keep every gene; the step therefore does nothing here rather than
failing. Worth knowing before somebody reads the notebook output and concludes the selection is
broken. It is a property of a targeted panel, not of this dataset.

## Where it runs

On the stand, `kestrel_sc` on the marketplace host's own ClickHouse — the same server that holds
`handbook`, which is why the container carries SUP-840's name. One server, three databases, three
reader accounts that cannot read each other's data.

| | |
|---|---|
| Host | the marketplace host — Azure VM `swarm-marketplace`, `20.240.194.2`, subnet address `10.10.1.10` |
| Container | `sup840-handbook-ch`, image `clickhouse/clickhouse-server:24.8`, `--restart unless-stopped` |
| Listener | `10.10.1.10:8123` only. Not the host's public address and not `0.0.0.0`: the NSG does not open 8123 |
| Data | Docker volume `sup840-handbook-data` on `/var/lib/clickhouse` |
| Reader account | `kestrel_reader`, declared in `/opt/marketplace/sup840-ch/users.xml`. Profile `readonly_bulk`, `allow_databases: kestrel_sc`, and the subnet in `networks` |
| Consumers | the gate on `10.10.1.11` reads it as this account and serves it on a port of its own; nothing else has a credential for it |

`readonly_bulk` exists because the handbook's `readonly` profile caps a result at 100,000 rows —
the right ceiling for a 64-document corpus and the wrong one for a count matrix, which is 1.1
million rows by construction. Same read-only lock, a ceiling of 5,000,000, and a 120-second clock
because the read crosses the gate.

Verified on 2026-11-24, after loading: `kestrel_reader` reads `kestrel_sc` and is refused
`cascade_tx` and `handbook` with `497 Not enough privileges`; `truncate` is refused with
`164 Cannot execute query in readonly mode`; the full 1,101,372-row projection completes under
the bulk profile; and `default` still answers `516 Authentication failed` over the subnet
address, which is the check that matters most — see the handbook dataset's README for why.

### Re-creating it from nothing

Steps 1 and 2 run on the marketplace host; step 3 needs the `.tsv` files, generated anywhere.

```bash
docker exec sup840-handbook-ch clickhouse-client -q "create database if not exists kestrel_sc"
docker exec sup840-handbook-ch clickhouse-client -q "
  create table if not exists kestrel_sc.sc_cells
  (cell_id String, batch String, donor String, site String, cell_type String)
  engine = MergeTree order by cell_id"
docker exec sup840-handbook-ch clickhouse-client -q "
  create table if not exists kestrel_sc.sc_genes (gene String, panel_class String)
  engine = MergeTree order by tuple()"
docker exec sup840-handbook-ch clickhouse-client -q "
  create table if not exists kestrel_sc.sc_counts (cell_id String, gene String, count UInt32)
  engine = MergeTree order by (cell_id, gene)"

datasets/kestrel-pbmc-panel/generate/generate.py --out-dir /tmp/kestrel
for t in sc_cells sc_genes sc_counts; do
  docker exec -i sup840-handbook-ch clickhouse-client \
    -q "insert into kestrel_sc.$t format TabSeparated" < /tmp/kestrel/$t.tsv
done
```

`users.xml` is the one thing this repository cannot regenerate, because it carries the reader's
password — a stand secret, held on the host and in the dataset's publisher secrets.
