# Where these notebooks come from, and what was changed

`00_dataset.ipynb` is ours. The other three are NVIDIA's, modified. Apache-2.0
section 4(b) asks that modified files carry prominent notices stating that they
were changed; this file is that notice, and each notebook repeats it in its first
cell.

## Upstream

- **Repository:** <https://github.com/NVIDIA-AI-Blueprints/single-cell-analysis-blueprint>
- **Commit:** `7c4148c59049563554708e01c378352586ab2f4a` (2026-04-27)
- **Licence:** Apache-2.0. Upstream's `LICENSE` is the stock Apache text with the
  `Copyright [yyyy] [name of copyright owner]` appendix left unfilled; it is carried
  into this directory verbatim as `LICENSE`. The copyright holders named anywhere in
  the work are the ones in each notebook's own header, kept in place below.
- **Notebook authors:** Severin Dicks, © [scverse](https://scverse.org), per each
  notebook's own header, which is left in place.
- Upstream ships no `NOTICE` file, so there is none to carry forward. Its
  dependency licences are listed in
  *RAPIDS-SingleCell Developer Blueprint OSS Licenses - SW Components.pdf* in
  that repository; every entry is MIT, BSD, Apache-2.0 or MPL-2.0.

| Ours | Upstream |
|---|---|
| `01_preprocessing.ipynb` | `notebooks/01_scRNA_analysis_preprocessing.ipynb` |
| `02_regulatory_activity.ipynb` | `notebooks/02_scRNA_analysis_extended.ipynb` |
| `03_pearson_residuals.ipynb` | `notebooks/03_scRNA_analysis_with_pearson_residuals.ipynb` |

## What was changed, and why

Four edits, applied mechanically. Each is marked `# Ported:` in the cell it
touches, so the diff against upstream is readable from inside the notebook.

1. **The download is gone.** Upstream's first data cell fetches
   `dli_census.h5ad` from `exampledata.scverse.org` with `wget` and re-reads it
   on the next cell. Both are replaced by `sc_workspace.load_matrix()`, which
   reads the dataset this deployment is bound to. A workflow whose first act is
   to pull a corpus off the public internet is the opposite of what this listing
   is for, and in a cluster space with no egress it would simply fail.
2. **The `obs` column names are the deployment's.** Upstream groups by `assay`
   and annotates with `cell_type` — columns of the corpus it downloads. They are
   now `scw.BATCH_KEY` and `scw.LABEL_KEY`, resolved from deployment parameters,
   and the loader cell checks both against the matrix and names the columns it
   does have when one is missing.
3. **`adata.var_names = adata.var.feature_name` is guarded.** Also a property of
   that corpus; a tenant's matrix is usually already indexed by gene name, and
   the unguarded line is an `AttributeError` in cell 14.
4. **Derived files go to the volume.** Upstream writes
   `h5/dli_decoupler.h5ad` into the working directory, which is thrown away with
   the pod. 01 now writes `/workspace/derived/preprocessed.h5ad` and 02 reads it
   from there.

Stored cell outputs were stripped. They are 11 MB of images and tables computed
from a corpus this deployment does not have, and they would be published inside
the deployment's evidence snapshot.

## What was not ported, and why

Four of the blueprint's seven notebooks are not here.

- **`04_scRNA_analysis_dask_out_of_core.ipynb`** — the 11-million-cell
  out-of-core workflow. Upstream calls for "a large multi-GPU system"; whether
  one H100 NVL with Dask carries it is unverified, and the stage-1 audit
  (SUP-838) recorded it as unverified rather than guessing. A listing that
  offered it would be promising something nobody has measured.
- **`05_spatial_demo.ipynb`** — needs `squidpy`, `spatialdata`, `ome-zarr` and
  the imaging stack. That is a different dependency closure and a different data
  interface (spatial transcriptomics, not a count matrix), so it belongs in a
  listing of its own rather than in this one's lock file.
- **`06_scRNA_analysis_1.0M_brain_example.ipynb`** and
  **`07_perturbation_analysis_invivo_brain_example.ipynb`** — both are built
  around specific public datasets they download. The workflow in 06 is 01's; 07
  is a perturbation analysis that would need a tenant's own design matrix to
  mean anything.
