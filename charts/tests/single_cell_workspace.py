#!/usr/bin/env python3
"""The workspace's data loader survives every shape the marketplace hands it.

    charts/tests/single_cell_workspace.py

This exists because of a deployment, not a design review. The first real
deployment of `rapids-singlecell-notebook` installed its whole attested
environment, seeded the workspace, and then CrashLoopBackOff'd on

    AttributeError: 'list' object has no attribute 'get'

`{{ data.matrices | json }}` renders the bound datasets as a JSON **array** —
one object per dataset. The platform's own type for a resolved slot
(`SlotBinding` in `libs/marketplace-spec/src/expressions/context.ts`) is an
object with `multiple` and `datasets`, which is what the loader was written
against, and it is not what reaches the chart. An empty slot renders `[]`.

Nothing in a render test can see this: the binding is in a Secret, the Secret is
correct, and the file is only read by Python inside a container. So the shapes
are asserted directly against the file the chart ships, with no container and no
cluster — which is also why this runs in CI rather than only on a stand.

The error mattered because `bootstrap.sh` runs under `set -e`: one traceback
before `exec jupyter lab` is a pod that never starts. That line is no longer
fatal, and this check is the other half of the fix.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
LOADER = ROOT / 'charts' / 'rapids-singlecell-notebook' / 'files' / 'sc_workspace.py'

# Every shape worth asserting, with the number of datasets the loader must find.
CASES: list[tuple[str, str | None, int]] = [
    ('an array of two bound datasets, as the marketplace renders it',
     json.dumps([{'dataset': 'a', 'interface': 'clickhouse-http/v1'}, {'dataset': 'b'}]), 2),
    ('an empty slot, which renders as []', '[]', 0),
    ('an object with a datasets key, the platform type', json.dumps({'multiple': True, 'datasets': [{'dataset': 'a'}]}), 1),
    ('an object with no datasets key', json.dumps({'multiple': True}), 0),
    ('an empty file, which is what an unset value writes', '', 0),
    ('a file that is not JSON at all', 'not json {', 0),
    ('an array with a non-object in it', json.dumps([{'dataset': 'a'}, 'junk', None]), 1),
    ('no file at all — the Secret key is optional', None, 0),
]


def load() -> dict:
    """The shipped module, executed without importing anndata or cupy."""
    namespace: dict = {'__name__': 'sc_workspace'}
    exec(compile(LOADER.read_text(), str(LOADER), 'exec'), namespace)  # noqa: S102
    return namespace


def main() -> int:
    if not LOADER.exists():
        print(f'  FAIL  {LOADER} does not exist')
        return 1

    failures = 0
    with tempfile.TemporaryDirectory() as directory:
        for label, content, expected in CASES:
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
                print(f'  FAIL  {label}: {type(error).__name__}: {error}')
                failures += 1
                continue

            if found != expected:
                print(f'  FAIL  {label}: found {found} dataset(s), expected {expected}')
                failures += 1
            elif not described:
                print(f'  FAIL  {label}: describe_binding() said nothing')
                failures += 1
            else:
                print(f'  ok    {label}')

    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
