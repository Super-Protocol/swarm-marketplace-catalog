#!/usr/bin/env python3
"""
Every first-party image this catalog pins is traced from its digest to the source
revision an attestation says built it.

A listing version reaches production by being merged to `main`: publish-charts.yml
packages the charts, and a deployment pulls the images those charts pin by digest.
Until this existed, the provenance of those digests was a sentence in a pull
request body — checked by a human reading it, and checked by nothing at all when
nobody read it. Listing 0.18.3 merged and deployed with no recorded review
(SUP-313, #66); its three digests happened to be right, which is luck rather than
a gate.

The direction matters and is the whole point: digest → attestation → commit, never
tag → digest. A scheduled release workflow can re-push the same `main-<sha>` tag
onto a freshly built image (SUP-316), so a tag no longer identifies one image and
resolving one proves nothing. A digest names bytes, and an attestation is the only
statement about where those bytes came from that the workflow which produced them
cannot forge.

What is checked, per pin:

  1. The pin is a real `sha256:` digest, not a tag and not the all-zero
     placeholder. A first-party image declaring no digest at all fails too — our
     own images are digest-pinned or they are not pinned.
  2. `gh attestation verify oci://<registry>/<repository>@<digest>` against the
     source repository derived from the image path exits 0, and the attestation's
     subject is the declared digest under the declared name. The signed bundle is
     fetched from the registry first and from the GitHub API second — see
     TRANSPORTS, where the private-source-repository problem is written down.
  3. Every first-party pin a listing bundles agrees about the source commit. A
     train is one source revision; a half-rebased re-pin — one chart moved, its
     sibling left behind — is the realistic mistake, and it renders, lints and
     deploys.
  4. The attestation names a revision at all, and one digest carrying attestations
     from two commits fails: the claim a reviewer reads then has no single answer.

Third-party images are reported, not attested: nothing in Super-Protocol signs
`ollama/ollama`. They appear in the table marked as such, so the verdict says
plainly which images the gate vouches for and which it only saw.

The commit and the signer workflow are read from the signing certificate
(`sourceRepositoryDigest`, `buildConfigURI`), which GitHub populates from the OIDC
token. The same commit also appears in the SLSA predicate, which the attested
workflow can set — so both are read and a disagreement is a failure rather than a
preference.

    scripts/verify-pins/verify_pins.py                       # this working tree, live gh
    scripts/verify-pins/verify_pins.py --root <dir>          # another tree (fixtures)
    scripts/verify-pins/verify_pins.py --attestations <file> # replay, no network
    scripts/verify-pins/verify_pins.py --record <file>       # record a replay file

Exit status is 0 when every pin is accounted for and 1 otherwise; every failure
line names the file, the key path and the digest, because "verify-pins failed" on
its own sends the reader back to re-running it by hand.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
PLACEHOLDER_DIGEST = "sha256:" + "0" * 64

# What makes an image ours. The source repository is derived from the path rather
# than from a table: `ghcr.io/super-protocol/<repo>/<image>` is built by
# `Super-Protocol/<repo>`, which is how every first-party image in this catalog is
# published. A table would be one more thing to forget to extend when a chart
# gains an image.
FIRST_PARTY_REGISTRY = "ghcr.io"
FIRST_PARTY_OWNER = "super-protocol"
FIRST_PARTY_SOURCE_OWNER = "Super-Protocol"

DEFAULT_REGISTRY = "docker.io"

# Where to look for the signed bundle, in order. The registry first, because the
# attestations of a **private** source repository are not readable through the
# GitHub API by this repository's workflow token — confidential-s3 is private, and
# its three digests came back HTTP 404 on the first CI run of this job while the
# public router's verified fine. The bundle in the registry needs no such access,
# and trusting it no more: it is verified against the same Sigstore certificate
# identity and the same subject digest, so the registry is transport and not an
# authority. A withheld or swapped bundle is a verification failure, never a pass.
#
# The API stays as the fallback, for an image whose release workflow does not push
# its bundle to the registry (`actions/attest-build-provenance` only does with
# `push-to-registry: true`).
TRANSPORTS = (
    ("registry", ["--bundle-from-oci"]),
    ("GitHub API", []),
)


@dataclass(frozen=True)
class Reference:
    """An image by registry, repository and digest — the only three things a pull needs."""

    registry: str
    repository: str
    digest: str

    def __str__(self) -> str:
        return f"{self.registry}/{self.repository}@{self.digest}"

    @property
    def name(self) -> str:
        return f"{self.registry}/{self.repository}"

    @property
    def first_party(self) -> bool:
        segments = self.repository.split("/")
        return self.registry == FIRST_PARTY_REGISTRY and segments[0] == FIRST_PARTY_OWNER and len(segments) >= 2

    @property
    def source_repository(self) -> str:
        return f"{FIRST_PARTY_SOURCE_OWNER}/{self.repository.split('/')[1]}"


@dataclass
class Pin:
    """Where a digest is written down, so a failure can name it."""

    origin: str  # the file
    key: str  # the key path inside it
    registry: str
    repository: str
    digest: str  # "" when the pin declares none
    tag: str
    declares_digest: bool

    @property
    def where(self) -> str:
        return f"{self.origin}: {self.key}"

    @property
    def reference(self) -> Reference | None:
        return Reference(self.registry, self.repository, self.digest) if DIGEST.match(self.digest) else None


@dataclass
class Attestation:
    """The fields a provenance verdict is made of, normalised out of `gh --format json`.

    Both arrays are read whole rather than at index 0. An attestation may name several
    subjects and several resolved dependencies, and the order is the producing
    workflow's to choose: reading `[0]` makes a check that one decoy entry ahead of the
    real one turns off, which for the predicate comparison means an advertised guarantee
    disabled with no signal at all.
    """

    subjects: list[tuple[str, str]]  # (name, digest) of every subject
    commit: str  # certificate sourceRepositoryDigest — from the OIDC token
    predicate_commits: list[str]  # every gitCommit in the predicate — the workflow's own claim
    workflow: str  # certificate buildConfigURI, which carries the ref as well


@dataclass
class Bundle:
    """A listing and the pins it deploys, or a chart nothing bundles yet."""

    name: str
    kind: str  # "listing" or "chart"
    pins: list[Pin] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Reading the pins out of the repository
# --------------------------------------------------------------------------- #


def split_reference(name: str) -> tuple[str, str]:
    """`ghcr.io/super-protocol/x` → ("ghcr.io", "super-protocol/x"); `ollama/ollama` → the default registry.

    A listing names an image as one string and may or may not carry the registry;
    a chart splits the two. Both have to normalise to the same reference or the
    same image counts twice.
    """
    head, _, rest = name.partition("/")
    if rest and ("." in head or ":" in head or head == "localhost"):
        return head, rest
    return DEFAULT_REGISTRY, name


def parse_image_string(value: str) -> dict:
    """`ghcr.io/super-protocol/x/y:main-abc@sha256:…` → the same four fields a block spells out.

    The order is forced: the digest comes off at the last `@`, then the tag at the
    last `:` *after* the last `/` — so `localhost:5000/x` keeps its registry port
    and `python:3.12-slim` does not keep its tag as part of the repository — and only
    what is left is split into registry and repository.
    """
    rest, _, digest = value.partition("@")
    tag = ""
    if ":" in rest.rsplit("/", 1)[-1]:
        rest, _, tag = rest.rpartition(":")
    registry, repository = split_reference(rest)
    return {
        "registry": registry,
        "repository": repository,
        "digest": digest,
        "tag": tag,
        "declares_digest": bool(digest),
    }


def image_blocks(node: object, path: list[str]) -> list[tuple[str, dict]]:
    """Every pin in a values tree, normalised to registry / repository / digest / tag.

    A mapping is a pin when it has a `repository`, keyed on that rather than on a key
    named `image`, because the key is not always that: patroni-postgresql pins its etcd
    bootstrap under `etcd.bootstrapImage`, and a nested one — the API chart's egress
    gatekeeper under `externalEndpoints.image` — is exactly the pin a flat read misses.
    A `repository` is enough; neither `digest` nor `tag` is required to notice the block,
    because a first-party image declaring neither — a chart leaving the pin to its
    listing — would otherwise pass by being invisible rather than by being pinned.

    A **string** under a key named `…image` is a pin too, and the same reasoning one
    level up: `image: ghcr.io/super-protocol/confidential-router/router-ui:main-deadbeef`
    is a first-party image pinned by tag, and recognising only mappings let it through
    with the job green and that chart simply absent from the verdict. Four charts write
    their third-party images this way today, so the form is in use here and not
    hypothetical; the existing first-party charts are safe from it only because their
    templates read `.Values.image.repository` and the string form breaks a golden — and a
    new chart brings its own goldens.
    """
    found: list[tuple[str, dict]] = []
    if isinstance(node, dict):
        if isinstance(node.get("repository"), str):
            # conversational-analyst-agent writes the host into `repository` and
            # declares no `registry` at all, which is the vendored chart's own
            # convention — so the split has to be read out of the repository when
            # the key is missing, or that image is reported under a registry it does
            # not come from.
            registry, repository = (
                (node["registry"], node["repository"])
                if node.get("registry")
                else split_reference(node["repository"])
            )
            found.append(
                (
                    ".".join(path) or ".",
                    {
                        "registry": registry,
                        "repository": repository,
                        "digest": (node.get("digest") or "").strip(),
                        "tag": str(node.get("tag") or ""),
                        "declares_digest": "digest" in node,
                    },
                )
            )
        for key, value in node.items():
            if isinstance(value, str) and str(key).lower().endswith("image"):
                found.append((".".join(path + [str(key)]), parse_image_string(value)))
            found += image_blocks(value, path + [str(key)])
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += image_blocks(value, path + [f"[{index}]"])
    return found


def chart_pins(root: Path, chart: str) -> list[Pin]:
    values_path = root / "charts" / chart / "values.yaml"
    if not values_path.exists():
        return []
    values = yaml.safe_load(values_path.read_text()) or {}
    pins = []
    for key, pin in image_blocks(values, []):
        pins.append(Pin(origin=f"charts/{chart}/values.yaml", key=key, **pin))
    return pins


def listing_pins(listing: str, definition: dict) -> list[Pin]:
    """The `images:` allow-list of every component.

    This is the list the cloud enforces at deploy time — the render path refuses a
    manifest naming an image that is not in it (marketplace specification §2.7) —
    so an unattested digest here is the production exposure, whatever the chart
    says. `declared_images.py` already holds the two files to the same digest; this
    reads both so the gate does not depend on that check having run.
    """
    pins = []
    for component in definition.get("components") or []:
        for index, image in enumerate(component.get("images") or []):
            # `name` is a repository in the specification, but parsing it the same way
            # a chart's string pin is parsed costs nothing and keeps a stray tag or
            # digest in it from becoming part of the repository.
            named = parse_image_string(str(image.get("name") or ""))
            pins.append(
                Pin(
                    origin=f"apps/{listing}/app.yaml",
                    key=f"components[{component.get('name')}].images[{index}]",
                    registry=named["registry"],
                    repository=named["repository"],
                    digest=(image.get("digest") or named["digest"] or "").strip(),
                    tag="",
                    declares_digest=True,  # the specification has no tag here: an entry is a digest
                )
            )
    return pins


def bundles(root: Path) -> list[Bundle]:
    """One bundle per listing, plus one per chart no listing bundles yet.

    The second half is not decoration: publish-charts.yml publishes every chart
    with a Chart.yaml whether or not a listing references it, and a chart is
    installable from that repository on its own.
    """
    found: list[Bundle] = []
    bundled_charts: set[str] = set()

    for app_yaml in sorted((root / "apps").glob("*/app.yaml")):
        listing = app_yaml.parent.name
        definition = yaml.safe_load(app_yaml.read_text()) or {}
        bundle = Bundle(name=listing, kind="listing")
        bundle.pins += listing_pins(listing, definition)
        for component in definition.get("components") or []:
            chart = ((component.get("source") or {}).get("chart")) or ""
            if chart and (root / "charts" / chart / "values.yaml").exists():
                bundled_charts.add(chart)
                bundle.pins += chart_pins(root, chart)
        found.append(bundle)

    for values_path in sorted((root / "charts").glob("*/values.yaml")):
        chart = values_path.parent.name
        if chart in bundled_charts:
            continue
        found.append(Bundle(name=chart, kind="chart", pins=chart_pins(root, chart)))

    return found


# --------------------------------------------------------------------------- #
# Attestations
# --------------------------------------------------------------------------- #


def parse_attestations(payload: list) -> list[Attestation]:
    parsed = []
    for entry in payload:
        result = entry["verificationResult"]
        certificate = result["signature"]["certificate"]
        statement = result["statement"]
        dependencies = statement["predicate"]["buildDefinition"].get("resolvedDependencies") or []
        parsed.append(
            Attestation(
                subjects=[
                    (subject.get("name", ""), "sha256:" + (subject.get("digest") or {}).get("sha256", ""))
                    for subject in statement.get("subject") or []
                ],
                commit=certificate.get("sourceRepositoryDigest", ""),
                predicate_commits=[
                    commit
                    for dependency in dependencies
                    if (commit := (dependency.get("digest") or {}).get("gitCommit"))
                ],
                workflow=certificate.get("buildConfigURI", ""),
            )
        )
    return parsed


def trim(payload: list) -> list:
    """Only the fields a verdict reads, so a recorded replay is reviewable.

    `gh attestation verify --format json` is 14 KB of signed bundle per image. A
    fixture carrying all of it is a fixture nobody reads.
    """
    kept = []
    for entry in payload:
        result = entry["verificationResult"]
        statement = result["statement"]
        kept.append(
            {
                "verificationResult": {
                    "signature": {"certificate": result["signature"]["certificate"]},
                    "statement": {
                        "subject": statement.get("subject"),
                        "predicateType": statement.get("predicateType"),
                        "predicate": {
                            "buildDefinition": {
                                "resolvedDependencies": statement["predicate"]["buildDefinition"].get(
                                    "resolvedDependencies"
                                )
                            }
                        },
                    },
                }
            }
        )
    return kept


class Verifier:
    """`gh attestation verify`, once per distinct reference, with the answers kept."""

    def __init__(self, recorded: dict | None = None):
        self.recorded = recorded
        self.answers: dict[str, tuple[list[Attestation] | None, str]] = {}
        # What a live lookup returned, before it was reduced to a verdict, so
        # `--record` writes a replay file out of the run that just happened rather
        # than asking ghcr.io the same questions a second time.
        self.raw: dict[str, dict] = {}

    def __call__(self, reference: Reference) -> tuple[list[Attestation] | None, str]:
        if str(reference) not in self.answers:
            self.answers[str(reference)] = self.lookup(reference)
        return self.answers[str(reference)]

    def lookup(self, reference: Reference) -> tuple[list[Attestation] | None, str]:
        if self.recorded is not None:
            entry = self.recorded.get(str(reference))
            if entry is None:
                return None, "no recorded attestation for this reference"
            if "error" in entry:
                return None, entry["error"]
            return parse_attestations(entry["attestations"]), ""

        errors = []
        for transport, flags in TRANSPORTS:
            payload, error = self.run_gh(reference, flags)
            if payload is None:
                errors.append(f"via the {transport}: {error}")
                continue
            try:
                parsed = parse_attestations(payload)
            except (KeyError, IndexError) as problem:
                errors.append(f"via the {transport}: unreadable attestation output: {problem}")
                continue
            self.raw[str(reference)] = {"attestations": trim(payload)}
            return parsed, ""

        error = "; ".join(errors)
        self.raw[str(reference)] = {"error": error}
        return None, error

    @staticmethod
    def run_gh(reference: Reference, flags: list[str]) -> tuple[list | None, str]:
        completed = subprocess.run(
            ["gh", "attestation", "verify", f"oci://{reference}", "--repo", reference.source_repository,
             "--format", "json", *flags],
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout).strip().splitlines()
            return None, (message[-1].strip() if message else "gh attestation verify failed")
        try:
            return json.loads(completed.stdout), ""
        except json.JSONDecodeError as problem:
            return None, f"unreadable attestation output: {problem}"

    def record(self) -> dict:
        """The lookups this run made, as a replay file."""
        return self.raw


# --------------------------------------------------------------------------- #
# The verdict
# --------------------------------------------------------------------------- #


@dataclass
class Verdict:
    pin: Pin
    status: str  # "ok", "third-party" or "FAIL"
    commit: str = ""
    workflow: str = ""
    detail: str = ""


def verify_pin(pin: Pin, verifier: Verifier) -> Verdict:
    reference = pin.reference

    # 1. The pin is a digest at all.
    if not pin.digest:
        if not pin.declares_digest:
            # A vendored subchart naming its own image by tag is the vendor's
            # contract and the listing rewrites it; ours are never left that way.
            placeholder = Reference(pin.registry, pin.repository, PLACEHOLDER_DIGEST)
            if placeholder.first_party:
                return Verdict(pin, "FAIL", detail=f"{placeholder.name} is first-party and declares no digest")
            return Verdict(pin, "third-party", detail=f"no digest declared; tag {pin.tag or '(none)'}")
        return Verdict(pin, "FAIL", detail=f"digest is empty — what is pinned is the tag {pin.tag or '(none)'}")
    if pin.digest == PLACEHOLDER_DIGEST:
        return Verdict(pin, "FAIL", detail="digest is the all-zero placeholder")
    if reference is None:
        return Verdict(pin, "FAIL", detail=f"digest is not a sha256 reference: {pin.digest}")

    # 2. Third-party images are seen, not vouched for.
    if not reference.first_party:
        return Verdict(pin, "third-party", detail="not published by Super-Protocol; no attestation expected")

    # 3. The attestation.
    attestations, error = verifier(reference)
    if attestations is None:
        return Verdict(pin, "FAIL", detail=f"attestation verification failed: {error}")
    if not attestations:
        return Verdict(pin, "FAIL", detail=f"no attestation for {reference} in {reference.source_repository}")

    for attestation in attestations:
        if not attestation.commit:
            # Without `sourceRepositoryDigest` there is no revision to report, and
            # a verdict that says "attested" beside an empty commit is worse than
            # one that says it could not tell.
            return Verdict(pin, "FAIL", detail="the attestation's signing certificate names no source commit")
        # The subject the pin names has to be among them, under the name the pin
        # declares. Searching rather than reading `subject[0]` so an attestation over
        # several artefacts cannot be rejected for listing ours second — and cannot be
        # accepted for listing something else first.
        if (reference.name, pin.digest) not in attestation.subjects:
            carried = ", ".join(f"{name}@{digest}" for name, digest in attestation.subjects) or "nothing"
            return Verdict(
                pin,
                "FAIL",
                detail=f"the attestation's subject is {carried}, the pin declares {reference.name}@{pin.digest}",
            )

        # The predicate's own account of the commit, every entry of it. An
        # attestation that names no `gitCommit` anywhere fails rather than skipping
        # the comparison: a guarantee that turns itself off when the field it reads
        # moves is not one.
        if not attestation.predicate_commits:
            return Verdict(
                pin,
                "FAIL",
                detail="the attestation's predicate resolves no source commit, so there is nothing to cross-check",
            )
        disagreeing = sorted({commit for commit in attestation.predicate_commits if commit != attestation.commit})
        if disagreeing:
            return Verdict(
                pin,
                "FAIL",
                detail=(
                    f"the signing certificate attests commit {attestation.commit} and the build's own "
                    f"predicate claims {', '.join(disagreeing)}"
                ),
            )

    commits = sorted({attestation.commit for attestation in attestations})
    if len(commits) > 1:
        # One digest, two source revisions: a rebuild that produced byte-identical
        # layers at a later commit, or a re-pushed release (SUP-316). Whichever it
        # is, "the revision this digest came from" has no single answer, so the
        # claim a reviewer would read cannot be made.
        return Verdict(pin, "FAIL", detail=f"{len(attestations)} attestations disagree about the commit: {', '.join(commits)}")

    return Verdict(pin, "ok", commit=commits[0], workflow=attestations[0].workflow)


def trains_of(verdicts: list[Verdict]) -> dict[str, dict[str, list[Verdict]]]:
    """The attested pins of one bundle, by source repository and then by commit.

    One bundle is one source revision — per source repository: a listing bundling
    images from two repositories of ours cannot share a commit between them, and
    holding it to one would be wrong rather than strict. More than one commit under
    a single repository is the half-rebased re-pin this gate exists to catch.
    """
    trains: dict[str, dict[str, list[Verdict]]] = {}
    for verdict in verdicts:
        if verdict.status != "ok" or not verdict.pin.reference:
            continue
        trains.setdefault(verdict.pin.reference.source_repository, {}).setdefault(verdict.commit, []).append(verdict)
    return trains


def verify(root: Path, verifier: Verifier) -> tuple[list[tuple[Bundle, list[Verdict]]], list[str]]:
    results = []
    failures: list[str] = []

    for bundle in bundles(root):
        verdicts = [verify_pin(pin, verifier) for pin in bundle.pins]
        results.append((bundle, verdicts))

        for verdict in verdicts:
            if verdict.status == "FAIL":
                failures.append(f"{bundle.kind} {bundle.name} — {verdict.pin.where}: {verdict.detail}")

        for source_repository, commits in trains_of(verdicts).items():
            if len(commits) > 1:
                failures.append(
                    f"{bundle.kind} {bundle.name} — pins from {source_repository} attest to "
                    f"{len(commits)} different commits:\n"
                    + "\n".join(
                        f"      {commit}  {verdict.pin.where} → {verdict.pin.reference}"
                        for commit, group in sorted(commits.items())
                        for verdict in group
                    )
                )

    return results, failures


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

SYMBOL = {"ok": "ok  ", "third-party": "--  ", "FAIL": "FAIL"}


def signer(verdict: Verdict) -> str:
    return verdict.workflow.split("/.github/workflows/")[-1] if verdict.workflow else "—"


def report(results: list[tuple[Bundle, list[Verdict]]], failures: list[str]) -> str:
    """The verdict in Markdown, for the job summary.

    Three sections rather than one table of everything: the failures, then what was
    attested, then what was not. A reviewer's question is "which revision is this
    train, and did anything go unvouched for" — and a flat table where six attested
    pins sit among fifty third-party rows answers it by making them look alike.
    """
    lines = ["## Image pin provenance", ""]

    if failures:
        lines += [f"**{len(failures)} failure(s).** Nothing below is a verdict on a pin that failed.", ""]
        for failure in failures:
            lines.append(f"- {failure}".replace("\n", "<br>"))
        lines.append("")
    else:
        lines += ["Every first-party pin traces to an attested commit, and every bundle agrees about which one.", ""]

    lines += ["### Attested — first-party images, digest → attestation → commit", ""]
    attested = False
    for bundle, verdicts in results:
        trains = trains_of(verdicts)
        if not trains:
            continue
        attested = True
        for source_repository, commits in sorted(trains.items()):
            heading = ", ".join(sorted(commits))
            lines += [f"**{bundle.kind} `{bundle.name}`** — {source_repository} @ `{heading}`", ""]
            lines += ["| Pinned at | Image | Digest | Signer workflow |", "| --- | --- | --- | --- |"]
            for commit in sorted(commits):
                for verdict in commits[commit]:
                    lines.append(
                        f"| `{verdict.pin.where}` | `{verdict.pin.reference.name}` | "
                        f"`{verdict.pin.digest}` | `{signer(verdict)}` |"
                    )
            lines.append("")
    if not attested:
        lines += ["None.", ""]

    unattested = sorted(
        {
            (f"{verdict.pin.registry}/{verdict.pin.repository}", verdict.pin.digest, verdict.detail)
            for _, verdicts in results
            for verdict in verdicts
            if verdict.status == "third-party"
        }
    )
    lines += [
        "<details><summary>"
        f"Not attested — {len(unattested)} third-party image(s) this gate saw and does not vouch for"
        "</summary>",
        "",
        "| Image | Digest | Why |",
        "| --- | --- | --- |",
    ]
    for name, digest, detail in unattested:
        lines.append(f"| `{name}` | `{digest or '—'}` | {detail} |")
    lines += ["", "</details>", ""]

    return "\n".join(lines) + "\n"


def print_console(results: list[tuple[Bundle, list[Verdict]]], failures: list[str]) -> None:
    for bundle, verdicts in results:
        if not verdicts:
            continue
        print(f"\n{bundle.kind} {bundle.name}")
        for verdict in verdicts:
            pin = verdict.pin
            reference = f"{pin.registry}/{pin.repository}@{pin.digest or '(no digest)'}"
            if verdict.status == "third-party":
                # One line: the gate's statement about these is that it has none.
                print(f"  {SYMBOL[verdict.status]} {reference}")
                continue
            print(f"  {SYMBOL[verdict.status]} {pin.where}")
            print(f"        {reference}")
            print(f"        {verdict.commit + '  ' + signer(verdict) if verdict.commit else verdict.detail}")
    print()
    if failures:
        print(f"verify-pins: {len(failures)} failure(s)\n")
        for failure in failures:
            print(f"  FAIL  {failure}")
        print()
    else:
        print("verify-pins: every first-party pin traces to an attested commit.\n")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=".", help="repository root to read apps/ and charts/ from")
    parser.add_argument("--attestations", help="replay recorded attestations instead of calling gh (no network)")
    parser.add_argument("--record", help="write the live lookups of this run to a replay file")
    parser.add_argument("--summary", help="write the Markdown table here (defaults to $GITHUB_STEP_SUMMARY)")
    arguments = parser.parse_args(argv)

    if arguments.record and arguments.attestations:
        parser.error("--record writes what a live run looked up; it has nothing to write from a replay")

    recorded = json.loads(Path(arguments.attestations).read_text()) if arguments.attestations else None
    verifier = Verifier(recorded)

    results, failures = verify(Path(arguments.root), verifier)

    print_console(results, failures)

    summary = arguments.summary or os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(report(results, failures))

    if arguments.record:
        Path(arguments.record).write_text(json.dumps(verifier.record(), indent=2, sort_keys=True) + "\n")
        print(f"recorded {len(verifier.answers)} lookup(s) to {arguments.record}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
