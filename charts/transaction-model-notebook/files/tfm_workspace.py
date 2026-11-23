"""The workbench's own module: where the transactions are, what the columns are
called, and whether the shipped model's vocabulary has ever seen anything like
them.

Upstream's notebooks begin by downloading the public TabFormer corpus — 24.4
million synthetic card transactions with IBM's column names — and work on it
from there. Neither half of that is what this listing is for: the data is the
tenant's, it arrives through a marketplace dataset slot or as a file they drop
into the workspace, and the point of the deployment is that it never leaves. This
module is the whole of that difference. The ported notebooks call
`load_transactions()` where the original called `urlretrieve`, and nothing else
about them changes.

Three things live here, and the third is the one with no counterpart upstream:

1. **The binding.** Where a bound dataset is read from (`/etc/tfm/binding.json`,
   out of a Secret, so the credentials in it are never part of the deployment's
   attested manifests), and what to do when nothing is bound.
2. **The column mapping.** Upstream's tokenizer is written against TabFormer's
   twelve columns by name. A tenant's extract calls them something else, so the
   deployment's parameters say which column is which and this module renames
   them — and synthesises the ones the extract does not have, using the same
   constant upstream's tokenizer would have produced for a missing field.
3. **The coverage check.** The shipped 29M-parameter checkpoint was trained with
   a 6,251-token vocabulary that enumerates US states, 3-digit ZIP prefixes, a
   fixed list of merchant category codes, seven dollar-amount buckets and
   customer indices 0-2999. Transactions from outside that world tokenise
   entirely to catch-alls — `STATE_XX`, `MCC_-1`, `CAT_GENERAL` — and the model
   then returns 512-dimensional embeddings that look exactly like embeddings and
   carry nothing. `coverage()` measures how much of the data lands inside that
   vocabulary and `check_coverage()` refuses to continue below the deployment's
   floor. A tenant whose data does not fit has a real option — notebook 03
   fine-tunes with a vocabulary built from their own fields — and the one thing
   they should not be able to do by accident is extract embeddings from a
   tokenizer that matched nothing.
"""

from __future__ import annotations

import json
import os
import pathlib

BINDING_PATH = pathlib.Path(os.environ.get('TFM_BINDING_FILE', '/etc/tfm/binding.json'))
DATA_DIR = pathlib.Path(os.environ.get('TFM_DATA_DIR', '/workspace/data'))
DERIVED_DIR = pathlib.Path(os.environ.get('TFM_DERIVED_DIR', '/workspace/derived'))

#: The checkpoint baked into the image, with its sha256 verified at build time.
MODEL_DIR = pathlib.Path(os.environ.get('TFM_MODEL_DIR', '/opt/tfm/models/decoder-foundation-model'))

#: The table (ClickHouse) or object directory (a file drop) holding the transactions.
TABLE = os.environ.get('TFM_TABLE', 'transactions')

def _whole_number(value, fallback: int) -> int:
    """An integer out of whatever the platform rendered into the environment.

    `int('2.5e+07')` raises, and `2.5e+07` is exactly what arrives: the listing's
    `maxRows` is an integer parameter, the marketplace carries it as a JSON
    number, and a YAML emitter writes a large float in scientific notation. The
    first real deployment of this chart died on that at *import* time — the
    whole workspace was unusable, before a single cell ran (SUP-870, measured on
    the gpu-blueprints cloud rather than found in review).
    """
    text = str(value or '').strip()
    if not text:
        return fallback
    try:
        return int(float(text))
    except ValueError:
        print(f'[tfm] TFM_MAX_ROWS={text!r} is not a number; using {fallback:,}')
        return fallback


#: The ceiling on rows read into one frame. A transaction history is the one kind
#: of table that is routinely larger than host RAM.
MAX_ROWS = _whole_number(os.environ.get('TFM_MAX_ROWS'), 25_000_000)

#: How much of the data has to tokenise into something the shipped vocabulary
#: contains before the notebooks will extract embeddings from it.
MIN_COVERAGE = float(os.environ.get('TFM_MIN_COVERAGE') or 0.5)  # float() already reads 5e-01

# ---------------------------------------------------------------------------
# The column mapping
# ---------------------------------------------------------------------------
#
# Keys are the canonical names upstream's `FinancialTokenizerPipeline.preprocess`
# expects (it lowercases and underscores them itself); values are what this
# deployment's parameters say the tenant's columns are called. The defaults are
# TabFormer's own names, so a deployment reading that corpus answers nothing.
#
# `timestamp` is the one that is not a straight rename: upstream's corpus carries
# four columns (Year, Month, Day, Time) and most extracts carry one. Both are
# accepted — four comma-separated names mean "use these as they are", one name
# means "parse it and derive them".

COLUMNS = {
    'user': os.environ.get('TFM_COL_USER', 'User'),
    'card': os.environ.get('TFM_COL_CARD', 'Card'),
    'amount': os.environ.get('TFM_COL_AMOUNT', 'Amount'),
    'merchant': os.environ.get('TFM_COL_MERCHANT', 'Merchant Name'),
    'mcc': os.environ.get('TFM_COL_MCC', 'MCC'),
    'zip': os.environ.get('TFM_COL_ZIP', 'Zip'),
    'state': os.environ.get('TFM_COL_STATE', 'Merchant State'),
    'entrymode': os.environ.get('TFM_COL_ENTRYMODE', 'Use Chip'),
    'city': os.environ.get('TFM_COL_CITY', 'Merchant City'),
    'timestamp': os.environ.get('TFM_COL_TIMESTAMP', 'Year,Month,Day,Time'),
    'label': os.environ.get('TFM_COL_LABEL', 'Is Fraud?'),
}

#: What each canonical column is called once this module has renamed it — the
#: TabFormer spelling, because that is what upstream's `preprocess` reads.
CANONICAL = {
    'user': 'User',
    'card': 'Card',
    'amount': 'Amount',
    'merchant': 'Merchant Name',
    'mcc': 'MCC',
    'zip': 'Zip',
    'state': 'Merchant State',
    'entrymode': 'Use Chip',
    'city': 'Merchant City',
    'label': 'Is Fraud?',
}

#: The value upstream's tokenizer would have produced for a field the extract
#: does not carry. Substituting it is a stated fact, not a silent zero: every
#: substitution is listed by `describe_columns()` and in the coverage report.
MISSING_DEFAULTS = {
    'card': 0,
    'mcc': -1,
    'zip': '00000',
    'state': 'XX',
    'entrymode': 'Chip Transaction',
    # Not a tokenizer field at all — the city is one of the thirteen raw features
    # the XGBoost baseline in notebook 01 is built from, and it is never
    # tokenised. A constant here costs that baseline one useless feature, which
    # is why it is not in the coverage report either.
    'city': 'UNKNOWN',
}

#: The four a transaction sequence cannot be built without. The chart refuses to
#: render without them; this is the same rule where a notebook can see it.
REQUIRED = ('user', 'amount', 'merchant', 'timestamp')

#: What the last `canonicalise()` had to make up, for the coverage report to
#: declare. Module state rather than an attribute on the frame on purpose: a
#: cuDF DataFrame turns `frame.anything = value` into a COLUMN, and pandas'
#: `.attrs` is not part of cuDF's API — either would be a silent data bug in
#: aid of bookkeeping.
LAST = {'substituted': [], 'labelled': False, 'rows': 0}


# ---------------------------------------------------------------------------
# The binding
# ---------------------------------------------------------------------------

def binding():
    """Whatever the marketplace wrote into the Secret, parsed, or None.

    The shape is not ours to choose. `{{ data.transactions | json }}` renders the
    bound datasets as a JSON **array** — one object per dataset — and an empty
    slot renders as `[]`. It is read defensively anyway: an object carrying a
    `datasets` key is the other plausible shape (the platform's own `SlotBinding`
    type has one), and a workbench that crashed on the wrong guess is a
    deployment that CrashLoopBackOffs with nothing wrong with it.
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
        entries = parsed.get('datasets')
        if entries is None and any(key in parsed for key in ('dataset', 'interface', 'connection')):
            # One dataset, rendered as itself rather than wrapped. The slot here
            # is `multiple: true`, so this is not the shape expected — but a
            # loader that answered "nothing bound" to a bound dataset would send
            # a tenant looking for a dataset that is already there, and this
            # costs three lines.
            entries = [parsed]
        entries = entries or []
    else:
        entries = []
    return [entry for entry in entries if isinstance(entry, dict)]


def local_files() -> list[pathlib.Path]:
    """Extracts the tenant dropped into the workspace, newest first."""
    if not DATA_DIR.exists():
        return []
    found = [
        path for path in DATA_DIR.rglob('*')
        if path.is_file() and path.suffix.lower() in {'.csv', '.parquet', '.pq'}
    ]
    return sorted(found, key=lambda path: path.stat().st_mtime, reverse=True)


def describe_binding() -> str:
    """One line, for the bootstrap log and the first cell of 00_environment."""
    bound = datasets()
    if not bound:
        files = local_files()
        if files:
            names = ', '.join(path.name for path in files[:5])
            return f'no dataset bound; {len(files)} extract(s) in {DATA_DIR}: {names}'
        return f'no dataset bound and no .csv/.parquet extract in {DATA_DIR}'
    return 'bound: ' + '; '.join(
        f'{entry.get("dataset")} ({entry.get("interface")})' for entry in bound
    )


def derived(name: str) -> str:
    """A path under /workspace/derived — where a notebook writes what the next one reads."""
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    return str(DERIVED_DIR / name)


# ---------------------------------------------------------------------------
# Reading the transactions
# ---------------------------------------------------------------------------

def timestamp_columns() -> list[str]:
    """The tenant's timestamp column, or the four they gave instead of one."""
    return [part.strip() for part in str(COLUMNS['timestamp']).split(',') if part.strip()]


def load_transactions(index: int = 0, path: str | None = None, limit: int | None = None):
    """The transactions the notebooks work on, as a cuDF frame with TabFormer's
    column names.

    `path` reads one file and ignores the binding — useful for a notebook picking
    up what an earlier one wrote. Otherwise the dataset at `index` of the slot is
    read, and with no binding the newest extract under /workspace/data.
    """
    limit = MAX_ROWS if limit is None else limit

    if path is not None:
        return canonicalise(_read_file(path, limit))

    bound = datasets()
    if bound:
        entry = bound[index]
        name = str(entry.get('interface') or '')
        if name.startswith('clickhouse-http/') or name.startswith('clickhouse-native/'):
            return canonicalise(_read_from_clickhouse(entry, limit))
        raise RuntimeError(
            f'{entry.get("dataset")} is bound over {name!r}, which this workbench cannot read. '
            'The listing accepts clickhouse-http/v1 — the one interface the marketplace '
            'gatekeeper can serve — or an extract uploaded into the workspace.'
        )

    files = local_files()
    if not files:
        raise RuntimeError(
            f'No dataset is bound to this deployment and {DATA_DIR} holds no .csv or .parquet '
            'file. Attach a dataset to the "Transaction history" slot, or upload an extract '
            f'into {DATA_DIR} with the file browser on the left.'
        )
    return canonicalise(_read_file(files[0], limit))


def _read_file(path, limit: int):
    """A .csv or .parquet extract off the volume."""
    import cudf

    path = pathlib.Path(path)
    if path.suffix.lower() in {'.parquet', '.pq'}:
        frame = cudf.read_parquet(path)
    else:
        frame = cudf.read_csv(path)
    if len(frame) > limit:
        # The tail, not the head: the most recent transactions are the ones worth
        # keeping, and a file is usually written in time order. Said out loud
        # rather than assumed — an extract in another order needs its own limit.
        print(
            f'[tfm] {path.name} has {len(frame):,} rows; keeping the last {limit:,} '
            '(TFM_MAX_ROWS). Pass limit= to read more, if the pod has the memory.'
        )
        frame = frame.tail(limit).reset_index(drop=True)
    return frame


def _read_from_clickhouse(entry: dict, limit: int):
    """The gated path: the transaction table a dataset owner published.

    The connection the deployment holds has no credentials of the dataset owner's
    database in it — it points at a plain listener inside this namespace, which
    re-originates every request to the marketplace gatekeeper over mutual TLS
    with this deployment's own certificate. The gate then decides, per request,
    against the evidence this deployment published. Nothing here needs to know
    that; it is an ordinary ClickHouse connection that stops working if this
    deployment changes.
    """
    import clickhouse_connect
    import cudf

    connection = entry.get('connection') or {}
    client = clickhouse_connect.get_client(
        host=connection.get('host'),
        port=int(connection.get('port') or 8123),
        database=connection.get('database'),
        username=connection.get('username') or 'default',
        password=connection.get('password') or '',
        secure=bool(connection.get('tls')),
    )

    table = TABLE
    select = ', '.join(_quote(name) for name in _wanted_columns())
    total = int(client.query(f'SELECT count() FROM {_quote(table)}').first_row[0])

    if total > limit:
        # The most recent rows, which is what the parameter promises. Ordering is
        # the server's work and it is not free; it happens only when the table is
        # bigger than the ceiling, and the frame is put back into ascending order
        # here so the sequences downstream read forwards.
        order = ', '.join(f'{_quote(name)} DESC' for name in timestamp_columns())
        query = f'SELECT {select} FROM {_quote(table)} ORDER BY {order} LIMIT {int(limit)}'
        print(
            f'[tfm] {table} has {total:,} rows; reading the most recent {limit:,} '
            '(TFM_MAX_ROWS).'
        )
    else:
        query = f'SELECT {select} FROM {_quote(table)}'

    # `canonicalise` sorts, so a most-recent-first read needs no reversing here.
    return cudf.DataFrame.from_pandas(client.query_df(query))


def _wanted_columns() -> list[str]:
    """Only the columns this deployment named, so a wide table is not transferred whole."""
    wanted = []
    for field, column in COLUMNS.items():
        if field == 'timestamp':
            wanted.extend(timestamp_columns())
        elif column:
            wanted.append(column)
    # dict order is insertion order and a tenant may name one column twice
    # (a single column as both the merchant and the category, say).
    seen, unique = set(), []
    for name in wanted:
        if name not in seen:
            seen.add(name)
            unique.append(name)
    return unique


def _quote(identifier: str) -> str:
    """A ClickHouse identifier, quoted, with no way out of the quotes.

    The column and table names come from deployment parameters — somebody
    typing into a form, not an attacker, but a column called `Amount"` would
    otherwise end the identifier and leave the rest of the string as SQL.
    """
    return '"' + str(identifier).replace('"', '""') + '"'


# ---------------------------------------------------------------------------
# The column mapping
# ---------------------------------------------------------------------------

def canonicalise(frame):
    """The tenant's frame, with TabFormer's column names and nothing missing.

    Three things happen: the named columns are renamed, the timestamp is turned
    into the Year / Month / Day / Time quartet upstream's `preprocess` reads, and
    a field the extract does not carry is filled with the constant upstream's
    tokenizer would have produced for it. What was substituted is recorded in
    `LAST` so the coverage report can say so.
    """
    import cudf

    missing = [field for field in REQUIRED if not COLUMNS.get(field)]
    if missing:
        raise RuntimeError(
            'This deployment names no column for: ' + ', '.join(missing) + '. '
            'A customer identity, an amount, a merchant and a timestamp are what a transaction '
            'sequence is made of; reconfigure the deployment with those columns.'
        )

    present = set(map(str, frame.columns))
    renames, substituted = {}, []

    for field, column in COLUMNS.items():
        if field == 'timestamp':
            continue
        if column and column in present:
            renames[column] = CANONICAL[field]
        elif field in REQUIRED:
            # Named and absent. A required field left EMPTY is caught above, with
            # a different message, because it is a different mistake.
            raise KeyError(
                f'This deployment names {column!r} as its {field} column and the extract has no '
                f'such column. Columns present: {", ".join(map(str, frame.columns))}. '
                'Reconfigure the deployment with one of those.'
            )
        else:
            # Either named and absent, or deliberately left empty — "my extract
            # has no such column". Both get the constant upstream's tokenizer
            # would have produced, and both are reported as substituted. Treating
            # only the first as a substitution is how the empty parameter ended
            # up producing a frame with the column missing altogether, which
            # upstream's `preprocess` then raised a bare KeyError on
            # (found by charts/tests/transaction_workspace.py).
            substituted.append(field)

    frame = frame.rename(columns=renames)
    frame = _with_time_columns(frame)
    frame = _normalise_values(frame)

    for field in substituted:
        if field == 'label':
            # No label is not a substitution: it is a deployment that cannot run
            # the fraud notebooks and can run everything else. Said once, here.
            continue
        frame[CANONICAL[field]] = MISSING_DEFAULTS[field]

    # Sorted, deliberately and here rather than in a notebook: the sequences the
    # tokenizer builds are per customer in time order, and a frame that arrived
    # in the storage engine's order — or most-recent-first, which is how a
    # row-capped read comes back — would produce sequences in that order and say
    # nothing about it.
    frame = frame.sort_values(['User', 'Year', 'Month', 'Day', 'Time']).reset_index(drop=True)

    LAST['substituted'] = [field for field in substituted if field != 'label']
    LAST['labelled'] = CANONICAL['label'] in set(map(str, frame.columns))
    LAST['rows'] = len(frame)
    return frame


def _normalise_values(frame):
    """Two columns put into the exact shape upstream's own code reads.

    Both of these are upstream assumptions about the public corpus rather than
    anything it checks, and both are silent when they are wrong — which is the
    kind of edit worth doing here rather than in five notebooks.

    `Amount` — upstream's corpus holds strings like `"$42.75"` and both
    `preprocess` and notebook 01 call `.str.replace('$', ...)` on the column. On
    a tenant's numeric amount column `.str` raises `AttributeError` in cuDF, so
    the column is cast to string once. `"42.75"` survives upstream's replace
    chain unchanged.

    `Is Fraud?` — upstream reads it as `(column == 'Yes') | (column == '1')`. A
    0/1 integer column compared against a string in cuDF does not mean "false";
    it is a type error or an all-false comparison depending on where it happens,
    and an all-false fraud label trains a baseline that reports an AUROC on no
    positives. So anything truthy becomes `'Yes'` and everything else `'No'`,
    once, here.
    """
    frame['Amount'] = frame['Amount'].astype('str')

    label = CANONICAL['label']
    if label in set(map(str, frame.columns)):
        column = frame[label]
        if column.dtype == 'object' or str(column.dtype).startswith('str'):
            upper = column.astype('str').str.upper().str.strip()
            positive = upper.isin(['YES', 'Y', 'TRUE', 'T', '1', 'FRAUD'])
        else:
            positive = column.fillna(0).astype('float64') != 0
        # "1"/"0" and then two exact single-character substitutions, rather than
        # a dict `replace` or a `map`: both of those have pandas semantics cuDF
        # does not implement identically for a boolean column, and the result of
        # getting this wrong is a label column that is silently all one value.
        frame[label] = (
            positive.astype('int8').astype('str')
            .str.replace('1', 'Yes', regex=False)
            .str.replace('0', 'No', regex=False)
        )
    return frame


def _with_time_columns(frame):
    """Year / Month / Day / Time, from the one column or the four."""
    import cudf

    names = timestamp_columns()
    present = set(map(str, frame.columns))

    if len(names) == 4:
        for name, canonical in zip(names, ('Year', 'Month', 'Day', 'Time')):
            if name not in present:
                raise KeyError(
                    f'This deployment names four timestamp columns ({", ".join(names)}) and the '
                    f'extract has no {name!r}. Columns present: '
                    f'{", ".join(map(str, frame.columns))}.'
                )
            if name != canonical:
                frame = frame.rename(columns={name: canonical})
        return frame

    if len(names) != 1:
        raise RuntimeError(
            f'The timestamp parameter holds {len(names)} column names ({", ".join(names)}). '
            'Give one column to be parsed, or four — Year, Month, Day, Time, in that order — '
            'to be used as they are.'
        )

    name = names[0]
    if name not in present:
        raise KeyError(
            f'This deployment names {name!r} as its timestamp column and the extract has no such '
            f'column. Columns present: {", ".join(map(str, frame.columns))}.'
        )

    stamp = cudf.to_datetime(frame[name])
    if stamp.isnull().any():
        bad = int(stamp.isnull().sum())
        raise RuntimeError(
            f'{bad:,} of {len(frame):,} rows have a {name!r} that does not parse as a timestamp. '
            'An unparseable timestamp would become hour 0 of a weekday the transaction did not '
            'happen on, which is a token the model reads as a fact.'
        )

    frame['Year'] = stamp.dt.year
    frame['Month'] = stamp.dt.month
    frame['Day'] = stamp.dt.day
    # Upstream's corpus carries "HH:MM" strings and `preprocess` parses the date
    # back out of exactly that shape, so the quartet is produced in its format
    # rather than in a tidier one.
    frame['Time'] = (
        stamp.dt.hour.astype('str').str.zfill(2) + ':' + stamp.dt.minute.astype('str').str.zfill(2)
    )
    return frame


def describe_columns() -> str:
    """What this deployment was told, in one block, for 00_environment."""
    lines = ['column mapping (this deployment -> upstream tokenizer):']
    for field, column in COLUMNS.items():
        if field == 'timestamp':
            names = timestamp_columns()
            shape = 'four columns, used as they are' if len(names) == 4 else 'one column, parsed'
            lines.append(f'  timestamp  <- {", ".join(names)}  ({shape})')
        elif column:
            lines.append(f'  {field:<10} <- {column}')
        else:
            default = MISSING_DEFAULTS.get(field)
            note = 'no fraud label; notebooks 01 and 05 need one' if field == 'label' \
                else f'not in this extract; substituted with {default!r}'
            lines.append(f'  {field:<10} <- (none)  ({note})')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# The environment
# ---------------------------------------------------------------------------

def require_label(notebook: str) -> None:
    """The fraud notebooks need a label, and say so before they do any work."""
    if LAST['labelled']:
        return
    raise RuntimeError(
        f'{notebook} scores fraud detection and this deployment names no fraud-label column '
        f'(or the extract has none). Everything up to and including the embeddings runs without '
        'a label — 02, 03 and 04 — but there is nothing here to score. Reconfigure the '
        "deployment with the label column's name if your extract carries one."
    )


def require_gpu() -> str:
    """The card, named, or an exception. Called by 00_environment before anything else."""
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError(
            'No CUDA device is visible to this pod. The tokenizer is cuDF and the model is '
            'torch: nothing in these notebooks runs without a card. A deployment that reaches '
            'this line has a GPU quota it did not get — check the cluster space, not the '
            'notebook.'
        )
    name = torch.cuda.get_device_name(0)
    total = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
    return f'{name} ({total:.0f} GiB)'


def describe_checkpoint() -> str:
    """What the baked-in checkpoint is, read off the checkpoint itself."""
    config = json.loads((MODEL_DIR / 'config.json').read_text())
    return (
        f'{MODEL_DIR} — {config.get("model_type")} decoder, '
        f'{config.get("num_hidden_layers")} layers, hidden {config.get("hidden_size")}, '
        f'vocabulary {config.get("vocab_size")}'
    )


# ---------------------------------------------------------------------------
# Does the shipped vocabulary know anything about this data?
# ---------------------------------------------------------------------------

def coverage(frame, sample: int = 1_000_000) -> dict:
    """How much of the data lands inside the shipped checkpoint's vocabulary.

    One number per vocabulary-pinned field — the fraction of rows whose value is
    a token the tokenizer actually has, rather than its catch-all — plus their
    mean as `overall`. The amount and the merchant are not in the list on
    purpose: amounts are bucketed, so every value is in range, and merchant names
    are hashed into 2,000 buckets, so every name hits one. Those two fields
    cover any data at all and would only dilute the number that matters.

    Measured on at most `sample` rows: this is a diagnostic printed before the
    work starts, and a full pass over 25 million rows to produce the same six
    fractions is thirty seconds nobody needs to wait for.
    """
    from src.tokenizer.financial_pipeline import ALL_STATES, CHIP_MAPPING, KNOWN_MCCS

    measured = frame if len(frame) <= sample else frame.sample(n=sample, random_state=42)
    rows = len(measured)
    # A notebook that reads a derived parquet rather than loading from the
    # binding has no record of what was substituted — the numbers below are
    # still measured from the data, and the report says which case it is instead
    # of claiming nothing was substituted.
    known = bool(LAST['rows'])
    substituted = set(LAST['substituted']) if known else set()
    report: dict = {
        'rows_measured': rows,
        'rows_total': len(frame),
        'substitution_known': known,
        'fields': {},
    }

    def record(field: str, fraction: float, note: str = '') -> None:
        report['fields'][field] = {
            'coverage': float(fraction),
            'substituted': field in substituted,
            'note': note,
        }

    mcc = measured['MCC'].fillna(-1).astype('int64')
    record('mcc', float(mcc.isin(KNOWN_MCCS).sum()) / rows,
           'a code outside the list tokenises to MCC_-1 and CAT_GENERAL')

    state = measured['Merchant State'].astype('str').str.upper().str.strip()
    record('state', float(state.isin(ALL_STATES).sum()) / rows,
           'a state outside the list tokenises to STATE_XX')

    chip = measured['Use Chip'].astype('str').str.upper().str.strip()
    record('entrymode', float(chip.isin(list(CHIP_MAPPING)).sum()) / rows,
           'an entry mode outside SWIPE/CHIP/ONLINE TRANSACTION tokenises to CHIP_UNK')

    user = measured['User'].astype('float64')
    record('user', float(((user >= 0) & (user <= 2999)).sum()) / rows,
           'the vocabulary has CUST_0 to CUST_2999; anything above is clipped onto CUST_2999, '
           'so customers share an identity')

    card = measured['Card'].astype('float64')
    record('card', float(((card >= 0) & (card <= 9)).sum()) / rows,
           'the vocabulary has CARD_0 to CARD_9; anything above is clipped onto CARD_9')

    zip3 = measured['Zip'].fillna('00000').astype('str').str.replace('.0', '', regex=False).str[:3]
    record('zip3', float(zip3.str.isdigit().sum()) / rows,
           'a non-numeric postcode has no 3-digit prefix to tokenise')

    values = [entry['coverage'] for entry in report['fields'].values()]
    report['overall'] = sum(values) / len(values)
    report['minimum'] = MIN_COVERAGE
    report['labelled'] = bool(LAST['labelled']) if known else (
        'Is Fraud?' in set(map(str, frame.columns)))
    return report


def format_coverage(report: dict) -> str:
    """The coverage report as something worth reading in a notebook."""
    lines = [
        f'vocabulary coverage, measured on {report["rows_measured"]:,} '
        f'of {report["rows_total"]:,} rows:',
    ]
    for field, entry in report['fields'].items():
        flag = '  (substituted — not in your extract)' if entry['substituted'] else ''
        lines.append(f'  {field:<10} {entry["coverage"] * 100:6.2f}%{flag}')
        if entry['coverage'] < 0.9:
            lines.append(f'             {entry["note"]}')
    lines.append(f'  {"overall":<10} {report["overall"] * 100:6.2f}%   (floor {report["minimum"] * 100:.0f}%)')
    lines.append(
        '  fraud label: ' + ('present' if report['labelled'] else
                             'absent — notebooks 01 and 05 have nothing to score')
    )
    if not report['substitution_known']:
        lines.append(
            '  (this frame was not loaded through load_transactions() in this kernel, so '
            'which fields were substituted is not known here — 00_environment says)'
        )
    return '\n'.join(lines)


def check_coverage(frame, minimum: float | None = None) -> dict:
    """The coverage report, and an exception when the shipped model cannot read this data.

    Called by 00_environment and again by the notebooks that extract embeddings.
    What it prevents is the failure mode this listing would otherwise have: a
    deployment that tokenises a European bank's transactions into 6,251 tokens it
    shares nothing with, extracts embeddings, plots a UMAP of them, trains an
    XGBoost on top and reports an AUROC — all of it arithmetic on the same
    handful of catch-all tokens, and none of it visibly wrong.
    """
    minimum = MIN_COVERAGE if minimum is None else minimum
    report = coverage(frame)
    print(format_coverage(report))
    if report['overall'] < minimum:
        raise RuntimeError(
            f'Only {report["overall"] * 100:.1f}% of this data lands inside the shipped '
            f'checkpoint\'s 6,251-token vocabulary, and this deployment\'s floor is '
            f'{minimum * 100:.0f}%. That checkpoint was pretrained on a US card-transaction '
            'corpus: US states, 3-digit ZIP prefixes, a fixed MCC list and customer indices '
            '0-2999. Embeddings extracted from it for this data would be arithmetic on '
            'catch-all tokens.\n\n'
            'Two ways forward, both real: fine-tune in 03_foundation_model_training.ipynb, '
            'which builds its vocabulary from your own fields and does not depend on the '
            'shipped weights; or map more of your columns onto the fields above (the report '
            'names which ones missed) and reconfigure the deployment. '
            'tfm_workspace.check_coverage(frame, minimum=0) is how you override this '
            'deliberately — in which case nothing downstream means what it appears to.'
        )
    return report
