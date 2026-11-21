#!/usr/bin/env python3
"""A listing that mounts gated data declares a digest, or it can never be served any.

    apps/tests/gated_digest.py                      # every definition in apps/
    apps/tests/gated_digest.py apps/foo/app.yaml    # one of them

`evidence.expectedDigest` reads like documentation — the value a deployment should match,
nice to have, declared when somebody gets round to measuring it. It is not. The marketplace
admits a deployment to the **gatekeeper** only when its listing version declares one:

    // gate-reconciler.service.ts, clientsFor()
    const declared = await this.declaredDigest(deployment);
    if (!host || !declared) {
      // Nothing to hold it to, or nowhere to check. Left out rather than admitted on the
      // marketplace's word alone.
      continue;
    }

So a listing with a `data.slots` entry and no declared digest produces deployments the gate
has never heard of, and every read of a gated dataset fails — with nothing in the deployment,
the chart or the schema to say why. That is how Conversational Analyst 4.0.3, 4.0.4 and 4.0.5
each shipped unable to read the clinic's card on the Latica stand, twice after the cause had
already been something else (SUP-711, 2026-11-21).

The rule is therefore: declare a slot, declare a digest. It cannot be checked by the JSON
Schema, which has no reason to couple two unrelated blocks, and it cannot be noticed in a
render, because the render is correct — the object that is missing is a row in the gate's
admitted-clients document.

A listing with no data slot is free of it: there is nothing to gate, and a digest stays
optional until somebody has measured one.

One listing is exempt by name, because this check found it rather than being written for it —
see `KNOWN_GAPS`.
"""

from __future__ import annotations

import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
HEX64 = set('0123456789abcdef')

# Listings that already had this defect when the check was written, with the issue that closes
# each one. The exemption is by name and by hand on purpose: a digest cannot be invented here,
# it has to be measured on a deployment, and that is a piece of work per listing rather than an
# edit. Nothing may be added to this list without an issue, and the list is meant to empty.
#
#   rag-agent  declares `corpus` and `feedback` and no digest, so no deployment of it can read
#              a gated dataset either. Found by this check on 2026-11-21; SUP-877.
KNOWN_GAPS = {'rag-agent': 'SUP-877'}


def definitions(arguments: list[str]) -> list[pathlib.Path]:
    if arguments:
        return [pathlib.Path(argument) for argument in arguments]
    return sorted((ROOT / 'apps').glob('*/app.yaml'))


def check(path: pathlib.Path) -> str | None:
    """The reason this definition fails, or None."""
    document = yaml.safe_load(path.read_text())
    slots = ((document.get('data') or {}).get('slots')) or []
    if not slots:
        return None

    digest = ((document.get('evidence') or {}).get('expectedDigest')) or ''
    names = ', '.join(str(slot.get('name')) for slot in slots)
    name = ((document.get('metadata') or {}).get('name')) or path.parent.name
    if not digest and name in KNOWN_GAPS:
        return None
    if not digest:
        return (
            f'declares the data slot(s) {names} and no evidence.expectedDigest, so the '
            'marketplace will never admit a deployment of it to the gatekeeper and every '
            'gated dataset it mounts is unreadable'
        )
    if len(digest) != 64 or not set(digest.lower()) <= HEX64:
        return f'evidence.expectedDigest is not a bare 64-character sha256 hex: {digest!r}'
    return None


def main(arguments: list[str]) -> int:
    failures = 0
    for path in definitions(arguments):
        reason = check(path)
        if reason:
            print(f'  FAIL  {path}: {reason}')
            failures += 1
        else:
            document = yaml.safe_load(path.read_text())
            name = ((document.get('metadata') or {}).get('name')) or path.parent.name
            if name in KNOWN_GAPS and not ((document.get('evidence') or {}).get('expectedDigest')):
                print(f'  note  {path}: no digest, exempt until {KNOWN_GAPS[name]} closes')
            else:
                print(f'  ok    {path}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
