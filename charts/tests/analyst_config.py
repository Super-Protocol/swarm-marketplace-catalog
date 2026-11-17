#!/usr/bin/env python3
"""The agent's configuration is attested, and carries no credential and no hostname.

    charts/tests/analyst_config.py charts/tests/cases/analyst-agent.yaml

This is the check the conversational-analyst listing's central claim rests on, and a golden
cannot be it. The document in question — `services.yaml`, the one every platform service reads
its own configuration out of — is rendered by a VENDORED subchart, which `run.sh`'s golden
filter deliberately excludes, and what matters about it is not its text but three properties:

1. **Nothing secret is in it.** It is a ConfigMap. The deployment bundle carries ConfigMaps in
   clear and the cloud digests them into evidence that is served publicly at
   `/.well-known/swarm-evidence`, so a password in there is a password published. Until 4.0.0
   every credential the platform has was in this document and the listing excluded the whole
   of it from the digest to compensate.

2. **Nothing per-deployment is in it either** — only the hostname ever was — because a
   document that differs per consumer makes the digest the consumer's rather than the
   version's, and then no two deployments can agree on an `expectedDigest`.

3. **Everything else IS in it.** That is the half worth keeping: the model endpoint, the
   embedding endpoint, the result thresholds, both egress allowlists and the absence of a
   speech-to-text provider are what a verifier reading the signed snapshot needs in order to
   believe anything about where this deployment sends data. Excluding `/data/services.yaml`
   buys (1) and (2) by giving up (3), which is the wrong trade for this product.

So the check reads the rendered ConfigMap, asserts that every value the chart was given as a
credential appears nowhere in it, that every `${NAME}` reference resolves to something the
overlay Secret carries, and that the disclosures above are present.
"""

import pathlib
import re
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = "charts/conversational-analyst-agent"
REFERENCE = re.compile(r"\$\{([A-Za-z0-9_]+)\}")

# What a verifier has to be able to read out of the snapshot, as (where, what) pairs over the
# `api` service's configuration.
DISCLOSED = [
    ("llmSmart.baseURL", "the endpoint the primary model is served from"),
    ("llmSmart.model", "which model that endpoint serves"),
    ("embeddings.baseUrl", "the endpoint the embeddings come from"),
    ("embeddings.model", "which embedding model"),
    ("embeddings.dimensions", "the width of the vectors, which the knowledge index is sized to"),
    ("mcp.egress.denyPrivateRanges", "whether a tool server may be a private address"),
    ("signals.webhook.egress.denyPrivateRanges", "the same question for a signal webhook"),
]


def dig(node, path):
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def main(case: str) -> int:
    values = yaml.safe_load((ROOT / case).read_text())
    overlay = dig(values, "aivo.config.configOverlay") or {}
    rendered = subprocess.run(
        ["helm", "template", "analyst", CHART, "--skip-tests",
         "--namespace", "conversational-analyst", "--values", case],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout

    configmaps = {}
    secrets = {}
    for doc in yaml.safe_load_all(rendered):
        if not doc:
            continue
        if doc["kind"] == "ConfigMap":
            configmaps[doc["metadata"]["name"]] = doc.get("data") or {}
        if doc["kind"] == "Secret":
            secrets[doc["metadata"]["name"]] = doc.get("stringData") or {}

    problems = []
    document = None
    for name, data in configmaps.items():
        if "services.yaml" in data:
            document = data["services.yaml"]
            break
    if document is None:
        print("  FAIL  no ConfigMap carries services.yaml — the configuration service has nothing to read")
        return 1

    # 1 and 2: every value the overlay supplies is a value the document does not carry.
    for key, value in overlay.items():
        if not value:
            continue
        if str(value) in document:
            problems.append(
                f"{key} appears verbatim in the attested services.yaml — the overlay exists so that "
                f"it does not"
            )

    # Every reference resolves, and nothing in the overlay is unused.
    referenced = set(REFERENCE.findall(document))
    for key in sorted(referenced - set(overlay)):
        problems.append(f"services.yaml refers to ${{{key}}} and the overlay does not carry it — the pod would not start")
    for key in sorted(set(overlay) - referenced):
        problems.append(f"the overlay carries {key} and nothing refers to it — a value nobody reads")

    # The overlay is a Secret, which is what makes any of this true.
    if not any(k.endswith("-config-overlay") for k in secrets):
        problems.append("the overlay is not rendered as a Secret, so it is not lifted out of the bundle")

    # 3: the disclosures.
    parsed = yaml.safe_load(document)
    api = None
    for entry in parsed["configs"]:
        if entry["name"] == "api":
            api = yaml.safe_load(entry["config"])
    if api is None:
        problems.append("services.yaml has no `api` entry")
    else:
        for path, what in DISCLOSED:
            if dig(api, path) in (None, ""):
                problems.append(f"{path} is not in the attested configuration, so a verifier cannot read {what}")
        # Speech to text is off by being absent, and "absent" is the assertion.
        if "stt" in api:
            problems.append("the api configuration carries an `stt` block — speech to text is a second outbound channel and this listing turns it off by omitting it")

    if problems:
        print(f"  FAIL  {case}")
        for problem in problems:
            print(f"          {problem}")
        return 1
    print(f"  ok    {case} ({len(referenced)} reference(s), {len(DISCLOSED)} disclosure(s), no stt)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "charts/tests/cases/analyst-agent.yaml"))
