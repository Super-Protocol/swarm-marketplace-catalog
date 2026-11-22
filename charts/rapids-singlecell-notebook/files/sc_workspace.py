"""The workspace's own module: where the data is, and what the columns are called.

The blueprint's notebooks each begin by downloading a public example corpus from
`exampledata.scverse.org` and end by writing the result next to it. Neither is
what this listing is for: the data is the tenant's, it arrives through a
marketplace dataset slot, and the point of the deployment is that it never
leaves. This module is the whole of that difference — the ported notebooks call
`load_matrix()` where the original called `wget`, and nothing else about them
changes.

Three sources, in the order `load_matrix` tries them:

1. a dataset bound into the listing's `matrices` slot and served through the
   gatekeeper or directly (`/etc/sc/binding.json`, from a Secret, so the
   credentials in it are never part of the deployment's attested manifests);
2. an `.h5ad` file under /workspace/data, which is where a browser upload lands;
3. nothing, and then a message saying so rather than a NameError forty cells
   later.

`BATCH_KEY` and `LABEL_KEY` exist for the same reason. The upstream notebooks
name `assay` and `cell_type`, which are columns of the corpus they download; a
tenant's matrix carries its own, and a notebook that hard-coded them would fail
in the middle of a Harmony call. They are deployment parameters, resolved here,
and checked against what the matrix actually has.
"""

from __future__ import annotations

import io
import json
import os
import pathlib

BINDING_PATH = pathlib.Path(os.environ.get('SC_BINDING_FILE', '/etc/sc/binding.json'))
DATA_DIR = pathlib.Path(os.environ.get('SC_DATA_DIR', '/workspace/data'))
DERIVED_DIR = pathlib.Path(os.environ.get('SC_DERIVED_DIR', '/workspace/derived'))

#: The `obs` column the ported notebooks treat as the batch, for QC plots and
#: for Harmony. Set by the deployment; `batch` by convention.
BATCH_KEY = os.environ.get('SC_BATCH_KEY', 'batch')

#: The `obs` column carrying a cell-type annotation, where the matrix has one.
#: The notebooks compare their own clustering against it.
LABEL_KEY = os.environ.get('SC_LABEL_KEY', 'cell_type')


# ---------------------------------------------------------------------------
# The binding
# ---------------------------------------------------------------------------

def binding():
    """Whatever the marketplace wrote into the Secret, parsed, or None.

    The shape is not ours to choose. `{{ data.matrices | json }}` renders the
    bound datasets as a JSON **array** — one object per dataset, which is also
    how the Conversational Analyst listing consumes it — and an empty slot
    renders as `[]`. It is read defensively anyway: an object carrying a
    `datasets` key is the other plausible shape (the platform's own `SlotBinding`
    type has one), and a workspace that crashed on the wrong guess is a
    deployment that CrashLoopBackOffs with the environment already installed.
    Verified against a real deployment on 2026-11-22, which is how the array
    shape is known rather than assumed.
    """
    if not BINDING_PATH.exists():
        return None
    text = BINDING_PATH.read_text().strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def datasets() -> list[dict]:
    """One entry per dataset the operator bound, in the order they chose."""
    parsed = binding()
    if isinstance(parsed, list):
        entries = parsed
    elif isinstance(parsed, dict):
        entries = parsed.get('datasets') or []
    else:
        entries = []
    return [entry for entry in entries if isinstance(entry, dict)]


def describe_binding() -> str:
    """One line, for the bootstrap log and the first cell of 00_dataset."""
    bound = datasets()
    if not bound:
        local = sorted(p.name for p in DATA_DIR.glob('*.h5ad')) if DATA_DIR.exists() else []
        if local:
            return f'no dataset bound; {len(local)} .h5ad file(s) in {DATA_DIR}: {", ".join(local)}'
        return f'no dataset bound and no .h5ad file in {DATA_DIR}'
    return 'bound: ' + '; '.join(
        f'{entry.get("dataset")} ({entry.get("interface")})' for entry in bound
    )


def derived(name: str) -> str:
    """A path under /workspace/derived — where a notebook writes what the next one reads."""
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    return str(DERIVED_DIR / name)


# ---------------------------------------------------------------------------
# Reading a matrix
# ---------------------------------------------------------------------------

def load_matrix(index: int = 0, path: str | None = None):
    """The AnnData the notebooks work on.

    `path` reads a file and ignores the binding — useful for a notebook picking
    up what an earlier one wrote. Otherwise the dataset at `index` of the slot is
    read, and with no binding the newest `.h5ad` under /workspace/data.
    """
    import anndata as ad

    if path is not None:
        return ad.read_h5ad(path)

    bound = datasets()
    if bound:
        entry = bound[index]
        name = str(entry.get('interface') or '')
        if name.startswith('s3-h5ad/'):
            return _read_h5ad_from_s3(entry)
        if name.startswith('clickhouse-http/'):
            return _read_matrix_from_clickhouse(entry)
        raise RuntimeError(
            f'{entry.get("dataset")} is bound over {name!r}, which this workspace cannot read. '
            'The listing accepts s3-h5ad/v1 and clickhouse-http/v1.'
        )

    local = sorted(DATA_DIR.glob('*.h5ad'), key=lambda p: p.stat().st_mtime, reverse=True)
    if not local:
        raise RuntimeError(
            f'No dataset is bound to this deployment and {DATA_DIR} holds no .h5ad file. '
            'Attach a dataset to the "Single-cell matrices" slot, or upload a file into '
            f'{DATA_DIR} with the file browser on the left.'
        )
    return ad.read_h5ad(local[0])


def _read_h5ad_from_s3(entry: dict):
    """An `.h5ad` object read straight out of the bucket the dataset owner named."""
    import anndata as ad
    import s3fs

    connection = entry.get('connection') or {}
    fs = s3fs.S3FileSystem(
        key=connection.get('accessKeyId'),
        secret=connection.get('secretAccessKey'),
        client_kwargs={
            'endpoint_url': connection.get('endpoint'),
            'region_name': connection.get('region') or 'us-east-1',
        },
        config_kwargs={'s3': {'addressing_style': 'path' if connection.get('pathStyle', True) else 'virtual'}},
    )

    bucket = connection['bucket']
    prefix = (connection.get('prefix') or '').strip('/')
    # `objects` is a comma-separated string, not a list: a connection property is a
    # scalar — the interface schema allows string, integer, number and boolean.
    objects = [name.strip() for name in str(connection.get('objects') or '').split(',') if name.strip()]
    if objects:
        keys = [f'{bucket}/{prefix}/{name}'.replace('//', '/') for name in objects]
    else:
        root = f'{bucket}/{prefix}' if prefix else bucket
        keys = sorted(key for key in fs.find(root) if key.endswith('.h5ad'))
    if not keys:
        raise RuntimeError(f'{entry.get("dataset")}: no .h5ad object under s3://{bucket}/{prefix}')

    # read_h5ad wants a file it can seek in, and h5py will not read a stream.
    # One object at a time, into memory: these are the matrices the card is
    # sized for, and the pod has host RAM for one of them.
    with fs.open(keys[0], 'rb') as handle:
        return ad.read_h5ad(io.BytesIO(handle.read()))


def _read_matrix_from_clickhouse(entry: dict, counts: str = 'sc_counts',
                                 cells: str = 'sc_cells', genes: str = 'sc_genes'):
    """A long-form count matrix assembled into AnnData.

    The gated path. The connection the deployment holds has no credentials in it
    — it points at a plain listener inside this namespace, which re-originates
    every request to the gatekeeper over mutual TLS with this deployment's own
    certificate. The gate then decides, per request, against the evidence this
    deployment published. Nothing here needs to know that; it is an ordinary
    ClickHouse connection that stops working if the deployment changes.
    """
    import anndata as ad
    import clickhouse_connect
    import numpy as np
    import pandas as pd
    from scipy import sparse

    connection = entry.get('connection') or {}
    client = clickhouse_connect.get_client(
        host=connection.get('host'),
        port=int(connection.get('port') or 8123),
        database=connection.get('database'),
        username=connection.get('username') or 'default',
        password=connection.get('password') or '',
        secure=bool(connection.get('tls')),
    )

    obs = client.query_df(f'SELECT * FROM {cells} ORDER BY cell_id')
    if obs.empty:
        raise RuntimeError(f'{entry.get("dataset")}: {cells} is empty')
    obs = obs.set_index('cell_id')

    try:
        var = client.query_df(f'SELECT * FROM {genes} ORDER BY gene').set_index('gene')
    except Exception:  # noqa: BLE001 — the table is optional, and its absence is not an error
        var = None

    long = client.query_df(f'SELECT cell_id, gene, count FROM {counts}')
    cell_index = {key: position for position, key in enumerate(obs.index)}
    gene_names = sorted(long['gene'].unique()) if var is None else list(var.index)
    gene_index = {key: position for position, key in enumerate(gene_names)}

    rows = long['cell_id'].map(cell_index).to_numpy()
    columns = long['gene'].map(gene_index).to_numpy()
    keep = ~(pd.isna(rows) | pd.isna(columns))
    matrix = sparse.csr_matrix(
        (long['count'].to_numpy()[keep], (rows[keep].astype(np.int64), columns[keep].astype(np.int64))),
        shape=(len(obs), len(gene_names)),
        dtype=np.float32,
    )

    adata = ad.AnnData(X=matrix, obs=obs)
    adata.var_names = pd.Index(gene_names)
    if var is not None:
        adata.var = var.reindex(adata.var_names)
    return adata


# ---------------------------------------------------------------------------
# Column names
# ---------------------------------------------------------------------------

def batch_key(adata, required: bool = True) -> str | None:
    """`BATCH_KEY` if the matrix has it, with a message naming what it does have."""
    return _key(adata, BATCH_KEY, 'batch', required)


def label_key(adata, required: bool = False) -> str | None:
    """`LABEL_KEY` if the matrix has it — annotation is genuinely optional."""
    return _key(adata, LABEL_KEY, 'cell-type annotation', required)


def _key(adata, name: str, role: str, required: bool) -> str | None:
    if name in adata.obs:
        return name
    if not required:
        return None
    available = ', '.join(map(str, adata.obs.columns)) or '(none)'
    raise KeyError(
        f'This deployment names {name!r} as its {role} column and the matrix has no such '
        f'column. Columns present: {available}. Reconfigure the deployment with one of those, '
        'or set sc_workspace.BATCH_KEY / LABEL_KEY in this notebook to override it here.'
    )
