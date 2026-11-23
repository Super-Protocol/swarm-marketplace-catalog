#!/usr/bin/env python3
"""The workbench's loader: every binding shape, the column mapping, and the one
check that stops this listing lying.

    charts/tests/transaction_workspace.py

Three reasons this exists, in increasing order of what it saves.

**The binding shape.** `charts/tests/single_cell_workspace.py` exists because the
first real deployment of the other notebook chart installed its whole attested
environment and then CrashLoopBackOff'd on `'list' object has no attribute
'get'`: `{{ data.<slot> | json }}` renders an ARRAY, and the platform's own
`SlotBinding` type reads like an object. The same eight shapes are asserted here,
plus a ninth — a bare single-dataset object — because this listing's slot could
be rendered that way and a loader that answered "nothing bound" to a bound
dataset is the same class of silent failure.

**The column mapping.** It is this listing's main product question: the tenant
says which of their columns is the customer, the amount, the merchant and the
timestamp, and the loader renames them onto the names upstream's tokenizer
reads, derives the Year/Month/Day/Time quartet, normalises the fraud label and
substitutes the constant upstream would have produced for a column the extract
does not have. Nothing in a Helm render can see any of that.

**The coverage check.** The published checkpoint was pretrained with a fixed
6,251-token vocabulary of US states, ZIP3 prefixes and a fixed MCC list.
Transactions from outside that world tokenise entirely to catch-alls and every
notebook downstream still produces embeddings, a UMAP and an AUROC. The loader
measures the fit and raises below the deployment's floor; this asserts that it
raises on data that does not fit and does not raise on data that does — which is
the only property of this listing that cannot be recovered after the fact by
looking at a number somebody published.

No GPU and no container: cuDF is stubbed with pandas, which implements every
operation the loader uses, and upstream's vocabulary module is stubbed with a
small representative set. The real lists live in the workbench image; what is
checked here is that the loader reads them from upstream's module rather than
carrying a copy of its own, and that it does the right arithmetic on them.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import types

try:
    import pandas
except ModuleNotFoundError:  # pragma: no cover - see the note below
    pandas = None

ROOT = pathlib.Path(__file__).resolve().parents[2]
LOADER = ROOT / 'charts' / 'transaction-model-notebook' / 'files' / 'tfm_workspace.py'

# A representative slice of upstream's `financial_pipeline` vocabulary. Small on
# purpose: the point is the arithmetic and the lookup, not a second copy of a
# list that lives in the image.
STUB_STATES = ['CA', 'NY', 'TX', 'XX', 'ONLINE']
STUB_MCCS = [-1, 5411, 5812, 7011]
STUB_CHIP = {
    'SWIPE TRANSACTION': 'SWIPE',
    'CHIP TRANSACTION': 'CHIP',
    'ONLINE TRANSACTION': 'ONLINE',
}

# A tenant whose data is the shape the checkpoint was trained on, and one whose
# data is not. Same columns, same mapping, opposite verdicts.
FITTING = {
    'customer_id': [1, 1, 2, 2, 3],
    'amount_eur': [42.75, 7.10, 980.0, 12.5, 3.2],
    'merchant_id': ['ACME', 'ACME', 'GLOBEX', 'INITECH', 'ACME'],
    'booked_at': ['2026-01-15 09:32', '2026-01-16 11:02', '2026-02-01 18:44',
                  '2026-02-03 07:15', '2026-03-09 21:00'],
    'card_index': [0, 0, 1, 1, 2],
    'category_code': [5411, 5812, 7011, 5411, 5411],
    'postcode': ['95113', '95113', '10001', '73301', '95113'],
    'region': ['CA', 'CA', 'NY', 'TX', 'CA'],
    'entry_mode': ['Chip Transaction', 'Swipe Transaction', 'Online Transaction',
                   'Chip Transaction', 'Chip Transaction'],
    'city': ['San Jose', 'San Jose', 'New York', 'Austin', 'San Jose'],
    'is_fraud': [0, 0, 1, 0, 0],
}

NOT_FITTING = {
    'customer_id': [900001, 900001, 900002, 900002, 900003],
    'amount_eur': [42.75, 7.10, 980.0, 12.5, 3.2],
    'merchant_id': ['ACME', 'ACME', 'GLOBEX', 'INITECH', 'ACME'],
    'booked_at': ['2026-01-15 09:32', '2026-01-16 11:02', '2026-02-01 18:44',
                  '2026-02-03 07:15', '2026-03-09 21:00'],
    'card_index': [11, 12, 13, 14, 15],
    'category_code': [9999, 9998, 9997, 9999, 9999],
    'postcode': ['SW1A 1AA', 'EC1A 1BB', 'W1A 0AX', 'M1 1AE', 'SW1A 1AA'],
    'region': ['LDN', 'LDN', 'MAN', 'BRS', 'LDN'],
    'entry_mode': ['contactless', 'contactless', 'ecom', 'contactless', 'contactless'],
    'city': ['London', 'London', 'Manchester', 'Bristol', 'London'],
    'is_fraud': ['no', 'no', 'yes', 'no', 'no'],
}

MAPPING = {
    'TFM_COL_USER': 'customer_id',
    'TFM_COL_AMOUNT': 'amount_eur',
    'TFM_COL_MERCHANT': 'merchant_id',
    'TFM_COL_TIMESTAMP': 'booked_at',
    'TFM_COL_CARD': 'card_index',
    'TFM_COL_MCC': 'category_code',
    'TFM_COL_ZIP': 'postcode',
    'TFM_COL_STATE': 'region',
    'TFM_COL_ENTRYMODE': 'entry_mode',
    'TFM_COL_CITY': 'city',
    'TFM_COL_LABEL': 'is_fraud',
}

BINDING_CASES: list[tuple[str, str | None, int]] = [
    ('an array of two bound datasets, as the marketplace renders it',
     json.dumps([{'dataset': 'a', 'interface': 'clickhouse-http/v1'}, {'dataset': 'b'}]), 2),
    ('an empty slot, which renders as []', '[]', 0),
    ('an object with a datasets key, the platform type',
     json.dumps({'multiple': True, 'datasets': [{'dataset': 'a'}]}), 1),
    ('a bare single-dataset object, which a non-multiple slot could render',
     json.dumps({'dataset': 'a', 'interface': 'clickhouse-http/v1', 'connection': {}}), 1),
    ('an object with no datasets key', json.dumps({'multiple': True}), 0),
    ('an empty file, which is what an unset value writes', '', 0),
    ('a file that is not JSON at all', 'not json {', 0),
    ('an array with a non-object in it', json.dumps([{'dataset': 'a'}, 'junk', None]), 1),
    ('no file at all — the Secret key is optional', None, 0),
]

failures = 0


def report(ok: bool, label: str, detail: str = '') -> None:
    global failures
    if ok:
        print(f'  ok    {label}')
    else:
        failures += 1
        print(f'  FAIL  {label}{": " + detail if detail else ""}')


def stub_cudf() -> None:
    """pandas, under cuDF's name, with the three entry points the loader uses."""
    cudf = types.ModuleType('cudf')
    cudf.DataFrame = pandas.DataFrame
    cudf.Series = pandas.Series
    cudf.to_datetime = pandas.to_datetime
    cudf.read_csv = pandas.read_csv
    cudf.read_parquet = pandas.read_parquet
    cudf.DataFrame.from_pandas = staticmethod(lambda frame: frame)
    sys.modules['cudf'] = cudf


def stub_vocabulary() -> None:
    """Upstream's `src.tokenizer.financial_pipeline`, with a representative slice.

    Stubbed rather than vendored: the loader must read the vocabulary from
    upstream's module — the one in the image, beside the weights it was built
    with — and a copy in this repository would be a second source of truth that
    drifts silently. If the loader ever stops importing it, these three names
    stop being used and the coverage cases below stop separating the two
    tenants.
    """
    for name in ('src', 'src.tokenizer', 'src.tokenizer.financial_pipeline'):
        sys.modules.pop(name, None)
    src = types.ModuleType('src')
    src.__path__ = []  # a package, so `src.tokenizer` can hang off it
    tokenizer = types.ModuleType('src.tokenizer')
    tokenizer.__path__ = []
    pipeline = types.ModuleType('src.tokenizer.financial_pipeline')
    pipeline.ALL_STATES = STUB_STATES
    pipeline.KNOWN_MCCS = STUB_MCCS
    pipeline.CHIP_MAPPING = STUB_CHIP
    sys.modules['src'] = src
    sys.modules['src.tokenizer'] = tokenizer
    sys.modules['src.tokenizer.financial_pipeline'] = pipeline


def load(environment: dict | None = None) -> dict:
    """The shipped module, executed with the deployment's environment."""
    import os

    previous = dict(os.environ)
    os.environ.update(environment or {})
    try:
        namespace: dict = {'__name__': 'tfm_workspace'}
        exec(compile(LOADER.read_text(), str(LOADER), 'exec'), namespace)  # noqa: S102
        return namespace
    finally:
        os.environ.clear()
        os.environ.update(previous)


def check_bindings() -> None:
    with tempfile.TemporaryDirectory() as directory:
        for label, content, expected in BINDING_CASES:
            namespace = load()
            path = pathlib.Path(directory) / 'binding.json'
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(content)
            namespace['BINDING_PATH'] = path
            try:
                found = len(namespace['datasets']())
                described = namespace['describe_binding']()
            except Exception as error:  # noqa: BLE001 — any raise here is the defect
                report(False, label, f'{type(error).__name__}: {error}')
                continue
            if found != expected:
                report(False, label, f'found {found} dataset(s), expected {expected}')
            elif not described:
                report(False, label, 'describe_binding() said nothing')
            else:
                report(True, label)


def check_timestamp_parameter() -> None:
    cases = [
        ('one timestamp column', 'booked_at', 1),
        ("upstream's four, as the default names them", 'Year,Month,Day,Time', 4),
        ('whitespace around the four', ' Year , Month , Day , Time ', 4),
    ]
    for label, value, expected in cases:
        namespace = load({'TFM_COL_TIMESTAMP': value})
        found = len(namespace['timestamp_columns']())
        report(found == expected, label, f'parsed {found} names, expected {expected}')

    # Two or three names is neither shape, and the message has to say so rather
    # than producing a frame with no hour in it.
    namespace = load({**MAPPING, 'TFM_COL_TIMESTAMP': 'date,time'})
    try:
        namespace['canonicalise'](pandas.DataFrame(FITTING))
        report(False, 'two timestamp columns are refused', 'it rendered a frame instead')
    except RuntimeError as error:
        report('one column to be parsed, or four' in str(error),
               'two timestamp columns are refused', str(error))


def check_identifier_quoting() -> None:
    namespace = load()
    quote = namespace['_quote']
    report(quote('Amount') == '"Amount"', 'an ordinary column name is quoted')
    report(quote('Amount"') == '"Amount"""',
           'a quote in a column name cannot end the identifier', quote('Amount"'))


def check_mapping() -> None:
    namespace = load(MAPPING)
    frame = namespace['canonicalise'](pandas.DataFrame(FITTING))

    for canonical in ('User', 'Amount', 'Merchant Name', 'MCC', 'Zip', 'Merchant State',
                      'Use Chip', 'Merchant City', 'Is Fraud?', 'Year', 'Month', 'Day', 'Time'):
        report(canonical in frame.columns, f'{canonical!r} is in the canonical frame')

    report(list(frame['Time'])[0] == '09:32',
           "the timestamp becomes upstream's HH:MM Time column", str(list(frame['Time'])[:2]))
    report(list(frame['Year'])[0] == 2026, 'the timestamp becomes a Year column')
    # 'object' in pandas 2, 'str' in pandas 3 — the image carries 3, and what
    # matters is that `.str` works on it rather than which name the dtype has.
    report(str(frame['Amount'].dtype) in {'object', 'str'},
           "Amount is a string, because upstream calls .str.replace('$') on it",
           str(frame['Amount'].dtype))
    report(sorted(set(frame['Is Fraud?'])) == ['No', 'Yes'],
           "a 0/1 label becomes upstream's Yes/No, which is what its own code compares against",
           str(sorted(set(frame['Is Fraud?']))))
    report(list(frame['User']) == sorted(frame['User']),
           'the frame is sorted by customer, so the sequences read forwards')

    # A column the extract does not carry: substituted with the constant
    # upstream's tokenizer would have produced, and SAID so.
    namespace = load({**MAPPING, 'TFM_COL_STATE': '', 'TFM_COL_MCC': ''})
    frame = namespace['canonicalise'](pandas.DataFrame(FITTING))
    report(set(frame['Merchant State']) == {'XX'},
           'a missing state column is substituted with the catch-all')
    report(set(frame['MCC']) == {-1}, 'a missing MCC column is substituted with -1')
    report(set(namespace['LAST']['substituted']) == {'state', 'mcc'},
           'what was substituted is recorded for the coverage report',
           str(namespace['LAST']['substituted']))
    report('substituted' in namespace['describe_columns'](),
           'describe_columns() says which columns were substituted')

    # A required column the extract does not have is an error naming the columns
    # it does have, not a KeyError forty cells later.
    namespace = load({**MAPPING, 'TFM_COL_AMOUNT': 'not_a_column'})
    try:
        namespace['canonicalise'](pandas.DataFrame(FITTING))
        report(False, 'a missing required column is refused', 'it rendered a frame instead')
    except KeyError as error:
        report('not_a_column' in str(error) and 'amount_eur' in str(error),
               'a missing required column is refused, naming the columns present', str(error))

    # And a deployment that names no amount column at all.
    namespace = load({**MAPPING, 'TFM_COL_AMOUNT': ''})
    try:
        namespace['canonicalise'](pandas.DataFrame(FITTING))
        report(False, 'a deployment naming no amount column is refused')
    except RuntimeError as error:
        report('amount' in str(error), 'a deployment naming no amount column is refused',
               str(error))

    # An unparseable timestamp is not hour zero of a weekday that did not happen.
    broken = dict(FITTING)
    broken['booked_at'] = ['2026-01-15 09:32', 'not a date', '2026-02-01 18:44',
                           '2026-02-03 07:15', '2026-03-09 21:00']
    namespace = load(MAPPING)
    try:
        namespace['canonicalise'](pandas.DataFrame(broken))
        report(False, 'an unparseable timestamp is refused')
    except Exception as error:  # noqa: BLE001 — pandas raises its own on a bad date
        report(True, 'an unparseable timestamp is refused', type(error).__name__)


def check_label_requirement() -> None:
    namespace = load({**MAPPING, 'TFM_COL_LABEL': ''})
    namespace['canonicalise'](pandas.DataFrame(FITTING))
    try:
        namespace['require_label']('01_dataset_baseline.ipynb')
        report(False, 'the fraud notebooks refuse to run without a label')
    except RuntimeError as error:
        report('01_dataset_baseline.ipynb' in str(error),
               'the fraud notebooks refuse to run without a label', str(error))

    namespace = load(MAPPING)
    namespace['canonicalise'](pandas.DataFrame(FITTING))
    try:
        namespace['require_label']('01_dataset_baseline.ipynb')
        report(True, 'and run when there is one')
    except RuntimeError as error:
        report(False, 'and run when there is one', str(error))


def check_coverage() -> None:
    namespace = load(MAPPING)
    frame = namespace['canonicalise'](pandas.DataFrame(FITTING))
    report_fitting = namespace['check_coverage'](frame)
    report(report_fitting['overall'] > 0.9,
           'data shaped like the corpus the checkpoint was trained on passes',
           f'{report_fitting["overall"]:.2f}')
    report(report_fitting['labelled'], 'and its label is seen')

    namespace = load(MAPPING)
    frame = namespace['canonicalise'](pandas.DataFrame(NOT_FITTING))
    try:
        namespace['check_coverage'](frame)
        report(False, 'data outside that vocabulary is refused',
               'it returned a report instead of raising')
    except RuntimeError as error:
        message = str(error)
        report('03_foundation_model_training.ipynb' in message,
               'data outside that vocabulary is refused, naming the notebook that does fit it',
               message)

    # The override exists and is deliberate; it must still work, and the report
    # it returns must be the honest one.
    namespace = load(MAPPING)
    frame = namespace['canonicalise'](pandas.DataFrame(NOT_FITTING))
    overridden = namespace['check_coverage'](frame, minimum=0)
    report(overridden['overall'] < 0.5,
           'minimum=0 overrides the floor and still reports the real number',
           f'{overridden["overall"]:.2f}')

    # Every vocabulary-pinned field is measured. A field quietly dropped from
    # this list is a field nobody is told about.
    expected = {'mcc', 'state', 'entrymode', 'user', 'card', 'zip3'}
    report(set(overridden['fields']) == expected,
           'every vocabulary-pinned field is in the report',
           str(sorted(overridden['fields'])))


def check_local_extract() -> None:
    """The no-dataset path: a .csv the tenant uploaded, and the row ceiling."""
    with tempfile.TemporaryDirectory() as directory:
        data = pathlib.Path(directory) / 'data'
        data.mkdir()
        pandas.DataFrame(FITTING).to_csv(data / 'extract.csv', index=False)

        namespace = load({**MAPPING, 'TFM_DATA_DIR': str(data)})
        namespace['BINDING_PATH'] = pathlib.Path(directory) / 'absent.json'
        frame = namespace['load_transactions']()
        report(len(frame) == 5, 'an uploaded .csv is read when no dataset is bound', str(len(frame)))

        namespace = load({**MAPPING, 'TFM_DATA_DIR': str(data), 'TFM_MAX_ROWS': '2'})
        namespace['BINDING_PATH'] = pathlib.Path(directory) / 'absent.json'
        frame = namespace['load_transactions']()
        report(len(frame) == 2, 'the row ceiling is applied to an uploaded extract', str(len(frame)))

        # Nothing bound and nothing uploaded: a message that says what to do,
        # not an IndexError.
        empty = pathlib.Path(directory) / 'empty'
        empty.mkdir()
        namespace = load({**MAPPING, 'TFM_DATA_DIR': str(empty)})
        namespace['BINDING_PATH'] = pathlib.Path(directory) / 'absent.json'
        try:
            namespace['load_transactions']()
            report(False, 'an empty deployment says what to attach')
        except RuntimeError as error:
            report('Transaction history' in str(error),
                   'an empty deployment says what to attach', str(error))


def check_unreadable_interface() -> None:
    """A dataset bound over an interface this workbench cannot read says so."""
    with tempfile.TemporaryDirectory() as directory:
        path = pathlib.Path(directory) / 'binding.json'
        path.write_text(json.dumps([
            {'dataset': 'lake-export', 'interface': 's3-parquet/v1', 'connection': {}},
        ]))
        namespace = load(MAPPING)
        namespace['BINDING_PATH'] = path
        try:
            namespace['load_transactions']()
            report(False, 'an interface the workbench cannot read is refused')
        except RuntimeError as error:
            report('clickhouse-http/v1' in str(error),
                   'an interface the workbench cannot read is refused, naming what it accepts',
                   str(error))


def main() -> int:
    if not LOADER.exists():
        print(f'  FAIL  {LOADER} does not exist')
        return 1

    # cuDF is stubbed with pandas, so pandas is this check's one dependency.
    # `.github/workflows/chart-tests.yml` installs it, which is where this runs
    # on every pull request; a developer's machine without it is told rather
    # than quietly passing.
    if pandas is None:
        print('  SKIP  pandas is not installed, so cuDF cannot be stubbed. '
              '`pip install pandas` to run this locally; CI always runs it.')
        return 0

    stub_cudf()
    stub_vocabulary()

    check_bindings()
    check_timestamp_parameter()
    check_identifier_quoting()
    check_mapping()
    check_label_requirement()
    check_coverage()
    check_local_extract()
    check_unreadable_interface()

    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
