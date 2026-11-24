#!/usr/bin/env python3
"""A listing that mounts gated data declares a digest, and says which configuration it is.

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

The second rule is the same defect one step further in, and it cost two more listings
(SUP-900). A digest is a property of a RENDER, and a deployment that mounts gated data does
not render what an ungated one does: the marketplace adds the egress sidecar's own objects to
it — a ConfigMap, a Deployment, a Service, and the Secret holding its client certificate —
and the cloud digests those too. A gated deployment and an ungated one therefore cannot share
a digest (platform-wide; SUP-842 recorded it). Which means a number measured with no dataset
attached, declared on a listing whose slot accepts the gateable interface, is a number the
gate will compare against a gated deployment and reject — and the only thing that listing's
data slot exists for is the gated read.

So the declared number has to BE the gated one, and the definition has to say so where a
reader can see it, because nothing in the value itself does. The declaration is a comment,
because `evidence` is `additionalProperties: false` in the specification and this is not a
field:

    evidence:
      # evidence-configuration: gated
      expectedDigest: "…"

An UNGATED deployment of such a listing then does not match the number on its own listing
page. That is the cheaper half of the trade: nothing refuses an ungated deployment, because
there is no gate in front of it to do the refusing. `apps/confidential-rag/app.yaml` has the
long version of this argument, and records the ungated number it measured in passing.

The rule cannot be checked by the JSON Schema, which has no reason to couple two unrelated
blocks, and it cannot be noticed in a render, because the render is correct — the object that
is missing is a row in the gate's admitted-clients document.

A listing with no data slot is free of both rules: there is nothing to gate, and a digest
stays optional until somebody has measured one. A listing whose slots accept no gateable
interface is free of the second one: today the gatekeeper carries ClickHouse over HTTP and
nothing else, so `s3-parquet/v1` or `clickhouse-native/v1` alone cannot be gated whatever a
dataset's access mode says.

Listings are exempt by name, with an issue each — see `KNOWN_GAPS` and `CONFIGURATION_GAPS`.
"""

from __future__ import annotations

import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
HEX64 = set('0123456789abcdef')

# The interface prefixes the marketplace gatekeeper can actually serve. The gate injects
# ClickHouse credentials into every request it forwards and rewrites the database the caller
# named; it speaks nothing else. Add to this when the gate learns a protocol, not when the
# specification registers one.
GATEABLE = ('clickhouse-http/',)

CONFIGURATION = re.compile(r'^\s*#\s*evidence-configuration:\s*(\S+)\s*$')

# Listings that already had this defect when the check was written, with the issue that closes
# each one. The exemption is by name and by hand on purpose: a digest cannot be invented here,
# it has to be measured on a deployment, and that is a piece of work per listing rather than an
# edit. Nothing may be added to either list without an issue, and both are meant to empty.
#
#   rag-agent  declares `corpus` and `feedback` and no digest, so no deployment of it can read
#              a gated dataset either. Found by this check on 2026-11-21; SUP-877.
KNOWN_GAPS = {'rag-agent': 'SUP-877'}

# Listings whose declared digest was measured with no dataset attached, so the gate refuses
# every gated deployment of them. Both are waiting on one shared GPU window to be re-measured
# gated, which is a deployment each and not an edit; SUP-900 owns the window and declares the
# numbers.
CONFIGURATION_GAPS = {
    'confidential-single-cell-analysis': 'SUP-900',
    'confidential-transaction-model': 'SUP-900',
}


def definitions(arguments: list[str]) -> list[pathlib.Path]:
    if arguments:
        return [pathlib.Path(argument) for argument in arguments]
    return sorted((ROOT / 'apps').glob('*/app.yaml'))


def name_of(document: dict, path: pathlib.Path) -> str:
    return ((document.get('metadata') or {}).get('name')) or path.parent.name


def gateable(slots: list) -> list[str]:
    """The gateable interfaces these slots accept, in the order they are declared."""
    accepted = []
    for slot in slots:
        for entry in (slot.get('accepts') or []):
            interface = str(entry.get('interface') or '')
            if interface.startswith(GATEABLE) and interface not in accepted:
                accepted.append(interface)
    return accepted


def configuration(path: pathlib.Path) -> tuple[str | None, str | None]:
    """The declared evidence configuration, or a reason it could not be read.

    Read from the text rather than the parsed document because it is a comment: `evidence` is
    `additionalProperties: false` in the specification, so there is no field to put it in and
    inventing one would fail schema validation. It has to sit inside the `evidence` block —
    that is where somebody reading the digest will look for it.
    """
    lines = path.read_text().splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line.rstrip() == 'evidence:')
    except StopIteration:
        return None, 'declares no top-level `evidence:` block'

    found = [match.group(1) for match in map(CONFIGURATION.match, lines[start:]) if match]
    if not found:
        return None, None
    if len(found) > 1:
        return None, (
            f'declares `# evidence-configuration:` {len(found)} times ({", ".join(found)}); '
            'one digest describes one configuration'
        )
    return found[0], None


def check(path: pathlib.Path) -> str | None:
    """The reason this definition fails, or None."""
    document = yaml.safe_load(path.read_text())
    slots = ((document.get('data') or {}).get('slots')) or []
    if not slots:
        return None

    digest = ((document.get('evidence') or {}).get('expectedDigest')) or ''
    names = ', '.join(str(slot.get('name')) for slot in slots)
    name = name_of(document, path)
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

    accepts = gateable(slots)
    if not accepts:
        return None
    if name in CONFIGURATION_GAPS:
        return None

    declared, problem = configuration(path)
    if problem:
        return problem
    if declared is None:
        return (
            f'accepts {", ".join(accepts)}, which the gatekeeper serves, and does not say '
            'which configuration its digest was measured in — add '
            '`# evidence-configuration: gated` inside the evidence block, and read the header '
            'of this file for why it has to be the gated one'
        )
    if declared == 'ungated':
        return (
            'declares the UNGATED digest while accepting '
            f'{", ".join(accepts)}: a deployment that mounts gated data gains the egress '
            "sidecar's objects and cannot produce this number, so the gate refuses every "
            'gated deployment of this listing — which is the only thing its data slot is for'
        )
    if declared != 'gated':
        return (
            f'declares `# evidence-configuration: {declared}`, which is neither `gated` nor '
            '`ungated`'
        )
    return None


def main(arguments: list[str]) -> int:
    failures = 0
    for path in definitions(arguments):
        reason = check(path)
        if reason:
            print(f'  FAIL  {path}: {reason}')
            failures += 1
            continue

        document = yaml.safe_load(path.read_text())
        name = name_of(document, path)
        digest = ((document.get('evidence') or {}).get('expectedDigest')) or ''
        if name in KNOWN_GAPS and not digest:
            print(f'  note  {path}: no digest, exempt until {KNOWN_GAPS[name]} closes')
        elif name in CONFIGURATION_GAPS and digest:
            print(f'  note  {path}: digest measured ungated, exempt until '
                  f'{CONFIGURATION_GAPS[name]} closes')
        else:
            declared, _ = configuration(path)
            suffix = f' ({declared})' if declared else ''
            print(f'  ok    {path}{suffix}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
