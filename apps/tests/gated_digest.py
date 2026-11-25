#!/usr/bin/env python3
"""A listing that mounts gated data declares a digest, and says which configuration it is.

    apps/tests/gated_digest.py                      # every definition in apps/
    apps/tests/gated_digest.py apps/foo/app.yaml    # one of them
    apps/tests/gated_digest.py --self-test          # the rules, over fixtures

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
Both lists are meant to empty themselves: a listed definition that would otherwise PASS fails,
naming the entry to delete. `--self-test` is where that is exercised, because it cannot be
exercised over apps/ — both lists are empty, so the branch is never reached on real input. That
is exactly how `CONFIGURATION_GAPS` came to claim the behaviour without having it: nothing any
run of this check could do would have noticed (SUP-913).
"""

from __future__ import annotations

import pathlib
import re
import sys
import tempfile

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
# every gated deployment of them.
#
# The list is EMPTY, and `check()` reads the `# evidence-configuration:` marker BEFORE it
# consults this list, so a listing on it that would otherwise pass fails — naming the
# exemption to delete. That order is the whole mechanism: the list empties itself instead of
# waiting for somebody to notice, which is the same discipline `KNOWN_INVALID` in
# `apps/tests/run.sh` has always had. It did not hold here as written: the exemption returned
# `None` before the marker was ever read, so a listed listing was skipped rather than held to
# anything. The claim had been copied from `run.sh`, where it is true; the code to make it true
# here arrived with SUP-913, with `--self-test` over both branches of it.
#
# The two notebook listings were on it for one day: SUP-900 measured both gated pairs in the
# card window of 2026-11-24 and declared the numbers, which is the only thing that ever takes
# a listing off this list.
CONFIGURATION_GAPS: dict[str, str] = {}


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

    # Stop at the next top-level key rather than at the end of the file. `evidence:` happens to
    # be last in every definition here today, so scanning to EOF gave the same answer — and
    # would have attributed a marker in a later block to this one the first time somebody moved
    # it. A top-level key is a line starting in column 0 that is neither blank nor a comment.
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line[:1] not in {'', ' ', '\t', '#'}:
            end = index
            break

    found = [match.group(1) for match in map(CONFIGURATION.match, lines[start:end]) if match]
    if not found:
        return None, None
    if len(found) > 1:
        return None, (
            f'declares `# evidence-configuration:` {len(found)} times ({", ".join(found)}); '
            'one digest describes one configuration'
        )
    return found[0], None


def configuration_problem(path: pathlib.Path, accepts: list[str]) -> str | None:
    """The reason this definition's evidence configuration is wrong, or None.

    Split out from `check()` so the exemption below can consult it rather than
    short-circuit it: an exemption that returns before the marker is read is an exemption
    nobody can ever be told to delete.
    """
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


def check(
    path: pathlib.Path,
    known_gaps: dict[str, str] | None = None,
    configuration_gaps: dict[str, str] | None = None,
) -> str | None:
    """The reason this definition fails, or None.

    The two exemption lists are parameters so `--self-test` can put a name on one; the
    defaults are the module's own, which is what `main()` uses.
    """
    known_gaps = KNOWN_GAPS if known_gaps is None else known_gaps
    configuration_gaps = CONFIGURATION_GAPS if configuration_gaps is None else configuration_gaps

    document = yaml.safe_load(path.read_text())
    slots = ((document.get('data') or {}).get('slots')) or []
    if not slots:
        return None

    digest = ((document.get('evidence') or {}).get('expectedDigest')) or ''
    names = ', '.join(str(slot.get('name')) for slot in slots)
    name = name_of(document, path)
    if not digest and name in known_gaps:
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

    # The marker is read BEFORE the exemption is consulted, so a listed definition that
    # would otherwise pass fails and names the entry to delete. The other order — the one
    # this check shipped with (SUP-913) — skips a listed definition entirely, which makes
    # the list permanent and the comment above it untrue.
    problem = configuration_problem(path, accepts)
    if name in configuration_gaps:
        if problem is None:
            return (
                f'is exempt in CONFIGURATION_GAPS until {configuration_gaps[name]} closes and '
                'now declares a gated digest — remove it from CONFIGURATION_GAPS in '
                'apps/tests/gated_digest.py, so the list stays empty'
            )
        return None
    return problem


# ---------------------------------------------------------------------------
# --self-test
# ---------------------------------------------------------------------------
#
# A check with an exemption list has two behaviours, and the one nobody exercises is the one
# that empties the list. This check's comment claimed that behaviour while `check()` returned
# before it could happen (SUP-913), and no run of the check over apps/ could have said so: the
# list is empty, so the branch is never taken on real input. Fixtures take it.

DIGEST = 'a' * 64

HEADER = """apiVersion: swarm.cloud/v1alpha1
kind: AppDefinition
metadata:
  name: {name}
"""

SLOTS = """data:
  slots:
    - name: corpus
      accepts:
        - interface: {interface}
"""

EVIDENCE = """evidence:
{marker}  expectedDigest: "{digest}"
"""


def fixture(directory: pathlib.Path, name: str, *, interface: str = 'clickhouse-http/v1',
            digest: str | None = DIGEST, marker: str | list[str] | None = None,
            slots: bool = True) -> pathlib.Path:
    markers = [marker] if isinstance(marker, str) else (marker or [])
    evidence = '' if digest is None else EVIDENCE.format(
        digest=digest,
        marker=''.join(f'  # evidence-configuration: {value}\n' for value in markers),
    )
    body = HEADER.format(name=name)
    if slots:
        body += SLOTS.format(interface=interface)
    body += evidence
    path = directory / f'{name}.yaml'
    path.write_text(body)
    return path


# (label, fixture keywords, exemption lists, the substring the verdict must contain or None)
SELF_TEST_CASES: list[tuple[str, dict, dict, dict, str | None]] = [
    ('a gated digest on a gateable slot passes',
     {'marker': 'gated'}, {}, {}, None),
    ('an ungated digest on a gateable slot fails',
     {'marker': 'ungated'}, {}, {}, 'declares the UNGATED digest'),
    ('a digest with no marker fails',
     {}, {}, {}, 'does not say which configuration'),
    ('a marker that is neither word fails',
     {'marker': 'measured'}, {}, {}, 'neither `gated` nor `ungated`'),
    ('two markers fail',
     {'marker': ['gated', 'ungated']}, {}, {}, 'one digest describes one configuration'),

    # The exemption, both ways round. This is the pair the claim above
    # CONFIGURATION_GAPS rests on.
    ('an ungated digest is exempt while it is on CONFIGURATION_GAPS',
     {'marker': 'ungated'}, {}, {'fix': 'SUP-1'}, None),
    ('no marker at all is exempt while it is on CONFIGURATION_GAPS',
     {}, {}, {'fix': 'SUP-1'}, None),
    ('a listing on CONFIGURATION_GAPS that now declares a gated digest FAILS, '
     'so the list empties itself',
     {'marker': 'gated'}, {}, {'fix': 'SUP-1'}, 'remove it from CONFIGURATION_GAPS'),
    ('and the exemption does not excuse a malformed digest',
     {'marker': 'gated', 'digest': 'not-a-digest'}, {}, {'fix': 'SUP-1'},
     'not a bare 64-character sha256 hex'),

    # The first rule, which the second one is one step further in.
    ('a gateable slot with no digest fails',
     {'digest': None}, {}, {}, 'no evidence.expectedDigest'),
    ('and is exempt while it is on KNOWN_GAPS',
     {'digest': None}, {'fix': 'SUP-2'}, {}, None),
    ('a slot the gatekeeper cannot serve needs no marker',
     {'interface': 's3-parquet/v1'}, {}, {}, None),
    ('a definition with no data slot at all is free of both rules',
     {'digest': None, 'slots': False}, {}, {}, None),
]


def self_test() -> int:
    failures = 0
    with tempfile.TemporaryDirectory() as directory:
        for label, keywords, known, gaps, expected in SELF_TEST_CASES:
            # The fixtures are named `fix` so one entry in either list hits them.
            path = fixture(pathlib.Path(directory), 'fix', **keywords)
            verdict = check(path, known_gaps=known, configuration_gaps=gaps)
            if expected is None and verdict is None:
                print(f'  ok    {label}')
            elif expected is None:
                print(f'  FAIL  {label}: it failed with {verdict!r}')
                failures += 1
            elif verdict is None:
                print(f'  FAIL  {label}: it passed, expecting {expected!r}')
                failures += 1
            elif expected in verdict:
                print(f'  ok    {label}')
            else:
                print(f'  FAIL  {label}: failed on {verdict!r}, expecting {expected!r}')
                failures += 1

    if failures:
        print(f'  {failures} self-test case(s) failed')
        return 1
    print(f'  {len(SELF_TEST_CASES)} self-test case(s) behave as the header says')
    return 0


def main(arguments: list[str]) -> int:
    if arguments and arguments[0] == '--self-test':
        return self_test()

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
