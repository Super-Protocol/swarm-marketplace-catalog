#!/usr/bin/env python3
"""Generate the Kestrel PBMC panel: a single-cell count matrix, as ClickHouse rows.

    datasets/kestrel-pbmc-panel/generate/generate.py --self-test
    datasets/kestrel-pbmc-panel/generate/generate.py --out-dir /tmp/kestrel

Why this exists: `Confidential Single Cell Analysis` declares a `clickhouse-http/v1` data
slot, which is the only interface the marketplace gatekeeper can serve — and nothing in this
catalogue was that shape. A matrix a lab wants to GATE therefore has to be three ClickHouse
tables in the long form `sc_workspace._read_matrix_from_clickhouse` assembles:

    sc_cells   one row per cell       cell_id, batch, donor, site, cell_type
    sc_genes   one row per gene       gene, panel_class
    sc_counts  one row per non-zero   cell_id, gene, count

Everything here is synthetic. No cell, donor or measurement comes from a real experiment, and
nothing is derived from one — it is a demonstration corpus whose only job is to make a gated
read, and the clustering that follows it, something a reader can check. The GENES are real
(`panel.py` says why: the notebooks flag `MT-`/`RPS` by prefix and score CollecTRI by symbol,
so invented symbols would score nothing while looking like they worked). The COUNTS are not.

Deterministic: one seed, one matrix, byte for byte, so a regenerated copy is the same data
rather than similar data.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from panel import FAMILIES, family_of, panel  # noqa: E402

SEED = 20261124
CELLS = 1800

# Four preparation runs over twelve donors at two sites. The batch is what
# `01_preprocessing.ipynb` integrates over with Harmony, so the runs carry a
# real per-gene shift rather than a cosmetic label — without one, Harmony has
# nothing to correct and the notebook's before/after embeddings are the same
# picture twice.
BATCHES = ['run_a', 'run_b', 'run_c', 'run_d']
SITES = ['kestrel-cambridge', 'kestrel-leuven']
DONORS = [f'D{index:02d}' for index in range(1, 13)]

# Three donors were sampled during an acute viral episode, which is the reason
# the interferon-stimulated genes are on the panel at all. It is a donor-level
# effect crossing every cell type, so it is the thing a cluster plot coloured by
# donor shows and a plot coloured by cell type does not.
INTERFERON_DONORS = {'D03', 'D07', 'D11'}
INTERFERON_MODULE = [
    'ISG15', 'IFI6', 'IFI44L', 'IFIT1', 'IFIT2', 'IFIT3', 'IFITM1', 'IFITM3',
    'MX1', 'MX2', 'OAS1', 'OAS2', 'OAS3', 'OASL', 'RSAD2', 'LY6E', 'XAF1',
    'EPSTI1', 'USP18', 'HERC5', 'SAMD9L', 'STAT1', 'IRF7',
]

# The nine populations, their share of the preparation, and the genes that make
# each one findable. A demonstration matrix whose clusters cannot be named is
# one nobody can check, so the programmes are separable on purpose.
POPULATIONS: list[tuple[str, float, list[str]]] = [
    ('CD4 T', 0.30, [
        'CD3D', 'CD3E', 'CD3G', 'TRAC', 'TRBC2', 'IL7R', 'LTB', 'LEF1', 'TCF7',
        'CD4', 'MAL', 'CD27', 'CD28', 'NOSIP', 'PIK3IP1', 'TRABD2A', 'AQP3',
        'GATA3', 'CD40LG', 'LDHB',
    ]),
    ('CD8 T', 0.17, [
        'CD3D', 'CD3E', 'CD8A', 'CD8B', 'TRAC', 'TRBC1', 'TRBC2', 'GZMK',
        'CCL5', 'NKG7', 'CST7', 'KLRG1', 'CTSW', 'HOPX', 'EOMES', 'IL32',
        'GZMA', 'KLRB1',
    ]),
    ('NK', 0.09, [
        'GNLY', 'NKG7', 'PRF1', 'GZMB', 'GZMH', 'KLRD1', 'KLRF1', 'KLRC1',
        'FGFBP2', 'SPON2', 'TYROBP', 'FCER1G', 'NCAM1', 'SH2D1B', 'CD247',
        'KLRK1', 'CST7', 'CTSW',
    ]),
    ('B', 0.09, [
        'MS4A1', 'CD79A', 'CD79B', 'BANK1', 'TCL1A', 'IGHM', 'IGHD', 'IGKC',
        'VPREB3', 'BLK', 'HLA-DRA', 'HLA-DQA1', 'CD74', 'PAX5', 'EBF1',
        'LINC00926', 'RALGPS2', 'CD19', 'CD22', 'FCRL1',
    ]),
    ('Plasma', 0.015, [
        'MZB1', 'JCHAIN', 'DERL3', 'SSR4', 'SEC11C', 'IGHG1', 'IGHG3',
        'IGHA1', 'IGLC2', 'IGLC3', 'XBP1', 'PRDM1', 'POU2AF1', 'TNFRSF17',
        'CD38', 'SPCS2', 'SPCS3',
    ]),
    ('CD14 Mono', 0.22, [
        'LYZ', 'S100A8', 'S100A9', 'S100A12', 'VCAN', 'FCN1', 'MNDA', 'CD14',
        'CTSS', 'CST3', 'SERPINA1', 'TYMP', 'ALOX5AP', 'PLBD1', 'RETN',
        'LGALS2', 'CSF3R', 'JAML', 'ANXA1',
    ]),
    ('CD16 Mono', 0.05, [
        'FCGR3A', 'MS4A7', 'MS4A4A', 'LST1', 'AIF1', 'COTL1', 'IFITM3',
        'CST3', 'CSF1R', 'CX3CR1', 'LILRB1', 'LILRB2', 'SIGLEC10', 'TYMP',
        'SERPINA1', 'CTSS',
    ]),
    ('cDC', 0.025, [
        'FCER1A', 'CLEC10A', 'CD1C', 'HLA-DQA1', 'HLA-DPB1', 'HLA-DRA',
        'CD74', 'CST3', 'CLEC4A', 'IRF8', 'CD83', 'LGALS2', 'ALDOA',
    ]),
    ('Platelet', 0.04, [
        'PPBP', 'PF4', 'GP9', 'GP1BA', 'TUBB1', 'NRGN', 'CAVIN2', 'CLU',
        'ITGA2B', 'TREML1', 'ACRBP', 'RGS18', 'SPARC', 'MYL9', 'TMEM40',
        'SELP', 'THBS1', 'VWF', 'HIST1H2AC',
    ]),
]

# Roughly what each family contributes to a cell's library before the
# population programme is applied. Ribosomal and housekeeping transcripts are
# most of a count matrix; that is what makes normalisation worth doing.
FAMILY_WEIGHT = {
    'mitochondrial': 9.0,
    'ribosomal': 7.0,
    'antigen-presentation': 2.0,
    'surface': 0.7,
    'transcription-factor': 0.45,
    'signalling': 0.5,
    'lineage': 0.9,
    'housekeeping': 3.0,
}

MARKER_GAIN = 14.0
LIBRARY_MEDIAN = 3200.0

# About one cell in twenty-five is a dying one: a high mitochondrial fraction
# and a small library. `01_preprocessing.ipynb` filters on `pct_counts_MT < 20`
# and it should have something to remove — a QC step that removes nothing is a
# QC step nobody can tell is working.
DEGRADED_FRACTION = 0.04


def gaussian(rng: random.Random) -> float:
    """A standard normal from `rng.random()` alone — Box-Muller, no library call.

    `random.gauss` and `random.lognormvariate` are not contractually stable
    across CPython releases; `random.random()` is the Mersenne Twister and is.
    A generator whose output depends on the interpreter is not deterministic in
    the sense this file claims to be.
    """
    first = rng.random()
    while first <= 0.0:
        first = rng.random()
    return math.sqrt(-2.0 * math.log(first)) * math.cos(2.0 * math.pi * rng.random())


def lognormal(rng: random.Random, mu: float, sigma: float) -> float:
    return math.exp(mu + sigma * gaussian(rng))


def poisson(rng: random.Random, mean: float) -> int:
    """A Poisson draw, Knuth below 30 and a rounded normal above it."""
    if mean <= 0.0:
        return 0
    if mean < 30.0:
        limit = math.exp(-mean)
        product = rng.random()
        count = 0
        while product > limit:
            count += 1
            product *= rng.random()
        return count
    value = int(round(mean + math.sqrt(mean) * gaussian(rng)))
    return max(value, 0)


def weighted_choice(rng: random.Random, weights: list[float]) -> int:
    total = sum(weights)
    cut = rng.random() * total
    running = 0.0
    for index, weight in enumerate(weights):
        running += weight
        if cut <= running:
            return index
    return len(weights) - 1


def build() -> tuple[list[tuple], list[tuple], list[tuple]]:
    """The three tables, as lists of tuples in the order they are written."""
    rng = random.Random(SEED)
    genes = panel()
    gene_family = {gene: family_of(gene) for gene in genes}
    interferon = set(INTERFERON_MODULE)

    # The baseline a gene is expressed at before anything about a cell is known.
    base = {
        gene: FAMILY_WEIGHT[gene_family[gene]] * lognormal(rng, 0.0, 0.9)
        for gene in genes
    }

    # Per-run, per-gene capture efficiency. This is the batch effect Harmony is
    # asked to remove, and it is multiplicative because that is what a
    # preparation difference actually looks like in a count matrix.
    batch_factor = {
        batch: {gene: lognormal(rng, 0.0, 0.33) for gene in genes}
        for batch in BATCHES
    }
    batch_depth = {batch: lognormal(rng, 0.0, 0.18) for batch in BATCHES}

    marker_sets = {name: set(markers) for name, _, markers in POPULATIONS}
    shares = [share for _, share, _ in POPULATIONS]
    names = [name for name, _, _ in POPULATIONS]

    cells: list[tuple] = []
    counts: list[tuple] = []

    for index in range(CELLS):
        cell_id = f'KPB{index + 1:05d}'
        batch = BATCHES[index % len(BATCHES)]
        donor = DONORS[weighted_choice(rng, [1.0] * len(DONORS))]
        site = SITES[DONORS.index(donor) % len(SITES)]
        population = names[weighted_choice(rng, shares)]
        markers = marker_sets[population]
        degraded = rng.random() < DEGRADED_FRACTION

        library = LIBRARY_MEDIAN * batch_depth[batch] * lognormal(rng, 0.0, 0.45)
        if population == 'Platelet':
            # Platelets carry a fraction of a leukocyte's transcriptome, and a
            # matrix that pretends otherwise makes the easiest cluster the
            # hardest one.
            library *= 0.28
        if degraded:
            library *= 0.45

        factor = batch_factor[batch]
        weights: list[float] = []
        for gene in genes:
            value = base[gene] * factor[gene]
            if gene in markers:
                value *= MARKER_GAIN
            if gene_family[gene] == 'mitochondrial':
                value *= 4.5 if degraded else 1.0
            if donor in INTERFERON_DONORS and gene in interferon:
                value *= 6.0
            weights.append(value)

        total = sum(weights)
        detected = 0
        for gene, weight in zip(genes, weights):
            count = poisson(rng, library * weight / total)
            if count:
                counts.append((cell_id, gene, count))
                detected += 1

        cells.append((cell_id, batch, donor, site, population))

    gene_rows = [(gene, gene_family[gene]) for gene in genes]
    return cells, gene_rows, counts


def tabseparated(rows: list[tuple]) -> str:
    """TabSeparated, escaped the way ClickHouse reads it back."""
    def field(value: object) -> str:
        if isinstance(value, int):
            return str(value)
        return (
            str(value)
            .replace('\\', '\\\\')
            .replace('\t', '\\t')
            .replace('\n', '\\n')
        )

    return ''.join('\t'.join(field(value) for value in row) + '\n' for row in rows)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', help='write sc_cells.tsv, sc_genes.tsv and sc_counts.tsv here')
    parser.add_argument('--self-test', action='store_true')
    arguments = parser.parse_args()

    cells, genes, counts = build()
    tables = {
        'sc_cells': tabseparated(cells),
        'sc_genes': tabseparated(genes),
        'sc_counts': tabseparated(counts),
    }

    if arguments.self_test:
        for name, text in tables.items():
            rows = text.count('\n')
            print(f'  {name:10} {rows:8,} rows  sha256 {digest(text)}')
        populations: dict[str, int] = {}
        for _, _, _, _, population in cells:
            populations[population] = populations.get(population, 0) + 1
        print('  populations ' + ', '.join(
            f'{name} {populations.get(name, 0)}' for name, _, _ in POPULATIONS))
        per_cell = len(counts) / len(cells)
        print(f'  {len(cells):,} cells x {len(genes):,} genes, '
              f'{per_cell:.0f} genes detected per cell, {len(counts):,} non-zero entries')
        for family, _ in FAMILIES:
            total = sum(count for _, gene, count in counts
                        if family_of(gene) == family)
            print(f'    {family:22} {total / sum(c for _, _, c in counts) * 100:5.1f}% of counts')
        return 0

    if not arguments.out_dir:
        parser.error('one of --out-dir or --self-test')

    directory = pathlib.Path(arguments.out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in tables.items():
        path = directory / f'{name}.tsv'
        path.write_text(text, encoding='utf-8')
        print(f'wrote {path}  {text.count(chr(10)):,} rows  sha256 {digest(text)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
