#!/usr/bin/env python3
"""The three containers of 4.0.2 that could not start, each one guarded by the fact that broke it.

    charts/tests/analyst_boot.py

A render is not a running deployment, and nothing in this suite noticed that listing 4.0.2 —
schema-valid, golden-stable, with a measured and declared evidence digest — brought up five of
its fifteen workloads. Evidence is collected when a cluster space is published, not when it
works, so the digest said nothing about it either. All three causes were found by deploying it
on `azure-test` and reading the namespace (SUP-711, 2026-11-21):

1. **Garage's RPC secret has to be hex.** The server wants 32 BYTES of it — 64 characters — and
   refuses to start otherwise: «Invalid RPC secret key: expected 32 bytes of random hex». The
   listing supplies the deployment's generated internal credential, which is 32 printable
   characters, so every deployment had `analyst-garage` in CrashLoopBackOff and no object
   storage for uploads or exports. The chart converts it now; this is the check that it still
   does, with the same shape of value a marketplace deployment hands it.

2. **A ConfigMap mount is not a flat directory of keys.** The kubelet's atomic writer puts the
   data in a timestamped directory, points `..data` at it and makes each key a symlink into
   that. The agent's configuration init container listed the mount and read every entry, so it
   read a directory and died with `EISDIR` — on the first boot and on every retry, which left
   `agent-config` in `Init:CrashLoopBackOff` and the five services that wait for it restarting
   behind it. The check runs the container's own script, taken out of the rendered Deployment,
   against a directory laid out the way the kubelet lays one out.

3. **The grounding Job's queries have to be the api's.** `PaginationInput` on agent 1.17 is
   `limit`/`offset`; the Job asked for `page`/`pageSize` and the api answered 400 on its
   first query, so no deployment of 4.0.x ever had a model row, a data source, a schema
   catalogue or a system prompt. The same run also showed the tenant being chosen by
   `organizations[0]` — Keycloak's order, not the deployment's — and the four result-policy
   numbers reaching the Job's `env` and nothing the cloud collects, so a data owner could
   not read them out of the signed snapshot. All three are checked here.

4. **Keycloak needs 2Gi, a heap cap, and probes that are not on a one-second fuse.** The floor
   came first: `production: false` means `start-dev`, which re-runs the Quarkus augmentation at
   every boot, and under a 1Gi limit it is OOMKilled mid-build — a deployment whose identity
   provider never starts has no agent either (SUP-711, peak ~880 MiB resident).

   The ceiling took another kill to find. `kc.sh` sizes the heap at 70% of the container limit
   whenever `JAVA_OPTS_KC_HEAP` is unset, and metaspace, the code cache, G1's bookkeeping and
   the direct buffers sit outside that heap and inside the cgroup — so the pod's ceiling was
   above its limit and the OOMKill was a matter of time, at 2.6 h and 15.6 h of uptime on the
   two Latica stands, each kill blocking sign-in for ~50 s (SUP-917). Raising the limit raises
   the heap with it, so what is asserted here is the RELATIONSHIP: the heap is capped by a
   number, and the limit clears that number by enough for everything outside it (SUP-925).

   The probes are the same defect seen from the Service's side. The vendored chart gives
   readiness `timeoutSeconds: 1` and polls liveness every second, both with a threshold of 3 —
   three seconds of a pod too busy to answer and Keycloak is out of the endpoints, or restarted.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]

# What a marketplace deployment actually supplies: `generated` parameters are 32 printable
# characters, never hex. The platform case already carries one, which is why it is the fixture.
PLATFORM_CASE = "charts/tests/cases/analyst-platform.yaml"
IDENTITY_CASE = "charts/tests/cases/analyst-identity.yaml"
AGENT_CASE = "charts/tests/cases/analyst-agent.yaml"
GROUNDING_CASE = "charts/tests/cases/analyst-grounding.yaml"

HEX64 = re.compile(r"^[0-9a-f]{64}$")
MEMORY_FLOOR_MIB = 2048

# What the JVM needs that is not heap: metaspace (capped at 256m by `kc.sh`), the code cache,
# G1's remembered sets and card table, ~60 thread stacks, Netty's direct buffers and the native
# allocator's own arenas. Measured on the pinned image as the gap between the container's peak
# and the heap capacity G1 reported at the time — 406 MiB with a 1434 MiB heap and 606 MiB with
# a 768 MiB one, the smaller heap paying for it in four times the collections — and the larger
# of the two is what has to be left outside, rounded up.
#
# This number is what makes the check bite. 2Gi minus the 1434 MiB `kc.sh` would pick for
# itself leaves 614 MiB, which is BELOW this reserve: the configuration that was OOMKilled on
# both Latica stands fails here, rather than passing because its limit happens to be large.
NONHEAP_RESERVE_MIB = 640

# A stall has to last this long before a probe acts. Not a G1 pause — those are milliseconds
# under a capped heap — but the seconds-long stall a node under memory pressure imposes on
# everything running in it, which is the state the stands' 503s were served in.
PROBE_PATIENCE_S = 60
HEAP_FLAG = "JAVA_OPTS_KC_HEAP"
HEAP_CONFIGMAP = "analyst-keycloak-java-opts"
XMX = re.compile(r"-Xmx(\d+)([kKmMgG]?)")


def render(chart: str, values: str) -> list[dict]:
    manifests = subprocess.run(
        ["helm", "template", "analyst", f"charts/{chart}", "--namespace", "conversational-analyst", "--values", values],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [document for document in yaml.safe_load_all(manifests) if document]


def memory_mib(value: str) -> float:
    text = str(value)
    units = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024, "Ti": 1024 * 1024,
             "K": 1000 / (1024 * 1024), "M": 1000**2 / 1024**2, "G": 1000**3 / 1024**2}
    for suffix, factor in units.items():
        if text.endswith(suffix):
            return float(text[: -len(suffix)]) * factor
    return float(text) / (1024 * 1024)


def check_garage_rpc_secret() -> list[str]:
    failures = []
    secrets = [d for d in render("conversational-analyst-platform", PLATFORM_CASE)
               if d.get("kind") == "Secret" and "garage.toml" in (d.get("stringData") or d.get("data") or {})]
    if not secrets:
        return ["garage: no Secret carrying garage.toml was rendered"]
    for secret in secrets:
        config = (secret.get("stringData") or {})["garage.toml"]
        match = re.search(r'^\s*rpc_secret\s*=\s*"([^"]*)"', config, re.MULTILINE)
        if not match:
            failures.append("garage: garage.toml has no rpc_secret")
            continue
        value = match.group(1)
        if not HEX64.match(value):
            failures.append(
                f"garage: rpc_secret is {len(value)} characters of non-hex ({value[:12]}…); "
                "Garage refuses to start on anything but 64 hex characters"
            )
        else:
            supplied = yaml.safe_load((ROOT / PLATFORM_CASE).read_text())["garage"]["rpcSecret"]
            print(f"  ok    garage: rpc_secret is 64 hex characters, from a {len(supplied)}-character non-hex credential")
    return failures


def heap_flags(documents: list[dict], container: dict) -> tuple[list[str], list[str]]:
    """Every value of JAVA_OPTS_KC_HEAP the Keycloak container is given, and where from.

    The container's own `env` wins over `envFrom`, and a later `envFrom` entry wins over an
    earlier one — but a second definition of this variable means the cap depends on which rule
    a reader happens to know, so the caller requires exactly one. The lookup has to follow
    `envFrom` because the cap is deliberately NOT an `extraEnvVars` entry: a definition's list
    replaces the chart's, and the conversational-analyst listing supplies one.
    """
    sources, values = [], []
    for entry in container.get("env", []):
        if entry.get("name") == HEAP_FLAG:
            sources.append("the container's own env")
            values.append(str(entry.get("value", "")))
    named = {
        (reference.get("configMapRef") or reference.get("secretRef") or {}).get("name")
        for reference in container.get("envFrom", [])
    }
    for document in documents:
        if document.get("kind") not in {"ConfigMap", "Secret"}:
            continue
        if document["metadata"]["name"] not in named:
            continue
        data = document.get("data") or document.get("stringData") or {}
        if HEAP_FLAG in data:
            sources.append(f"{document['kind']}/{document['metadata']['name']}")
            values.append(str(data[HEAP_FLAG]))
    return values, sources


def max_heap_mib(flags: str) -> float | None:
    match = XMX.search(flags)
    if not match:
        return None
    scale = {"": 1 / 1024**2, "k": 1 / 1024, "K": 1 / 1024, "m": 1, "M": 1,
             "g": 1024, "G": 1024}[match.group(2)]
    return float(match.group(1)) * scale


def check_the_listing_leaves_the_heap_configmap_alone() -> list[str]:
    """The listing must not point `extraEnvVarsCM` somewhere else.

    The cap lives in a ConfigMap because a definition REPLACES `extraEnvVars` rather than
    appending to it — but `extraEnvVarsCM` is a scalar, and a definition that supplies one of
    its own replaces this chart's just as completely, leaving a deployment that renders, boots,
    serves sign-in, and sizes its heap at a share of the node again. Checked here rather than
    left to a render, because the chart's own case files cannot see what the listing says.
    """
    definition = yaml.safe_load((ROOT / "apps" / "conversational-analyst" / "app.yaml").read_text())
    for component in definition["components"]:
        if component["name"] != "identity":
            continue
        declared = ((component["deployment"]["values"]["base"].get("keycloak") or {})
                    .get("extraEnvVarsCM"))
        if declared is None:
            print("  ok    keycloak: the listing leaves extraEnvVarsCM to the chart")
            return []
        if declared != HEAP_CONFIGMAP:
            return [
                f"apps/conversational-analyst/app.yaml: the identity component sets "
                f"keycloak.extraEnvVarsCM to {declared!r}, which replaces the chart's "
                f"{HEAP_CONFIGMAP!r} and takes the heap cap with it"
            ]
        print(f"  ok    keycloak: the listing's extraEnvVarsCM is {declared}")
        return []
    return ["apps/conversational-analyst/app.yaml: no component named 'identity'"]


def check_keycloak_sizing() -> list[str]:
    """The limit, the heap cap that has to fit under it, and the probes' patience."""
    failures = []
    documents = render("conversational-analyst-identity", IDENTITY_CASE)
    containers = [
        container
        for document in documents
        if document.get("kind") == "StatefulSet"
        for container in document["spec"]["template"]["spec"]["containers"]
        if container["name"] == "keycloak"
    ]
    if not containers:
        return ["keycloak: no Keycloak container was rendered"]

    for container in containers:
        resources = container.get("resources") or {}
        limit = (resources.get("limits") or {}).get("memory")
        request = (resources.get("requests") or {}).get("memory")
        if limit is None:
            failures.append("keycloak: no memory limit declared")
            continue
        limit_mib = memory_mib(limit)
        if limit_mib < MEMORY_FLOOR_MIB:
            failures.append(
                f"keycloak: memory limit is {limit}; the Quarkus augmentation is OOMKilled "
                f"below {MEMORY_FLOOR_MIB}Mi and the deployment then has no identity provider"
            )
        elif request is None or memory_mib(request) != limit_mib:
            failures.append(
                f"keycloak: memory request is {request} against a limit of {limit}; a cluster "
                "space's LimitRange sets maxLimitRequestRatio: 1 and refuses the container"
            )
        else:
            print(f"  ok    keycloak: memory limit is {limit}, requests equal to limits")

        values, sources = heap_flags(documents, container)
        if not values:
            failures.append(
                f"keycloak: nothing gives the container {HEAP_FLAG}, so `kc.sh` sizes the heap at "
                f"70% of {limit} and the JVM's ceiling is above the pod's — which is an OOMKill "
                "at whatever uptime the heap finishes expanding at (SUP-925)"
            )
            continue
        if len(values) > 1:
            failures.append(
                f"keycloak: {HEAP_FLAG} is defined {len(values)} times ({', '.join(sources)}); "
                "one of them wins and which is not something a reader should have to derive"
            )
            continue
        heap_mib = max_heap_mib(values[0])
        if heap_mib is None:
            failures.append(
                f"keycloak: {HEAP_FLAG} is {values[0]!r}, which sets no -Xmx. A percentage of the "
                "container limit is what this version replaced: raising the limit raises the heap "
                "with it and the kill only moves"
            )
        elif limit_mib - heap_mib < NONHEAP_RESERVE_MIB:
            failures.append(
                f"keycloak: -Xmx is {heap_mib:.0f}Mi under a {limit} limit, leaving "
                f"{limit_mib - heap_mib:.0f}Mi for metaspace, the code cache, G1's bookkeeping, "
                f"the thread stacks, the direct buffers and the native arenas — measured at up "
                f"to 606Mi, so at least {NONHEAP_RESERVE_MIB}Mi has to be left outside the heap"
            )
        else:
            print(
                f"  ok    keycloak: heap capped at {heap_mib:.0f}Mi by {sources[0]}, "
                f"{limit_mib - heap_mib:.0f}Mi left outside it"
            )

        for name in ("readinessProbe", "livenessProbe"):
            probe = container.get(name)
            if not probe:
                failures.append(f"keycloak: no {name}")
                continue
            timeout = probe.get("timeoutSeconds", 1)
            period = probe.get("periodSeconds", 10)
            threshold = probe.get("failureThreshold", 3)
            patience = period * threshold
            if timeout < 5:
                failures.append(
                    f"keycloak: {name} allows {timeout}s for an answer. One second is the "
                    "vendored default and it evicts a healthy Keycloak off a node that is "
                    "briefly busy (SUP-917)"
                )
            elif patience < PROBE_PATIENCE_S:
                failures.append(
                    f"keycloak: {name} acts after {patience}s ({period}s x {threshold}); a stall "
                    f"has to outlast {PROBE_PATIENCE_S}s before sign-in is taken away"
                )
            else:
                print(f"  ok    keycloak: {name} waits {timeout}s per probe and {patience}s in all")
    return failures


def check_config_init_reads_a_configmap_mount() -> list[str]:
    """Run the init container's own script against the kubelet's atomic-writer layout."""
    if shutil.which("node") is None:
        print("  note  node is not installed, so the configuration init script is unchecked")
        return []

    script = None
    for document in render("conversational-analyst-agent", AGENT_CASE):
        if document.get("kind") != "Deployment":
            continue
        for container in document["spec"]["template"]["spec"].get("initContainers") or []:
            # `command` in the chart, `args` if a future one splits it; take whichever carries
            # the script rather than assuming the shape of the pod spec.
            parts = list(container.get("command") or []) + list(container.get("args") or [])
            script_parts = [part for part in parts if "readdirSync" in str(part)]
            if script_parts:
                script = script_parts[-1]
    if script is None:
        return ["agent: no init container carrying the configuration resolution script was rendered"]

    with tempfile.TemporaryDirectory() as workspace:
        root = pathlib.Path(workspace)
        # Exactly what the kubelet writes: the data in a timestamped directory, `..data`
        # pointing at it, and the key a symlink through that.
        data = root / "config" / "..2026_11_21_15_04_00.1234567890"
        data.mkdir(parents=True)
        (data / "services.yaml").write_text("services:\n  api:\n    password: ${ANALYST_DB_PASSWORD}\n")
        (root / "config" / "..data").symlink_to(data.name)
        (root / "config" / "services.yaml").symlink_to(pathlib.Path("..data") / "services.yaml")
        overlay = root / "overlay"
        overlay.mkdir()
        (overlay / "overlay.json").write_text(json.dumps({"ANALYST_DB_PASSWORD": 'pa"ss'}))
        destination = root / "conf"
        destination.mkdir()

        localised = (script
                     .replace("'/etc/aivo/config'", f"'{root / 'config'}'")
                     .replace("'/app/conf'", f"'{destination}'")
                     .replace("'/etc/aivo/config-overlay/overlay.json'", f"'{overlay / 'overlay.json'}'"))
        result = subprocess.run(["node", "-e", localised], capture_output=True, text=True)
        if result.returncode != 0:
            # The line that says what happened is the thrown error, not node's version banner.
            lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
            reason = next((line for line in lines if "Error" in line or "error" in line), lines[-1] if lines else "no output")
            return [
                "agent: the configuration init script fails on a ConfigMap mount as the kubelet "
                f"writes one:\n        {reason}"
            ]
        written = (destination / "services.yaml")
        if not written.exists():
            return ["agent: the configuration init script resolved nothing"]
        if '"pa\\"ss"' not in written.read_text():
            return ["agent: the resolved document does not carry the JSON-encoded credential"]
        print("  ok    agent: the configuration init script resolves a kubelet-written ConfigMap mount")
    return []


def check_grounding_speaks_the_api_1_17_talks() -> list[str]:
    """The three facts the grounding Job needs in order to do anything at all."""
    failures = []
    script = (ROOT / "charts/conversational-analyst-grounding/files/ground.py").read_text()

    # 1. The pagination shape. 1.17 answers 400 to the other one, on the first query.
    if "pageSize" in script or re.search(r"pagination:\s*\{\s*page\b", script):
        failures.append(
            "grounding: a query still pages with page/pageSize; agent 1.17's PaginationInput "
            "takes limit/offset and refuses the document with HTTP 400"
        )
    else:
        print("  ok    grounding: every paged query uses limit/offset")

    # 2. The tenant by alias. Taking the first organisation is Keycloak's order, not the
    #    deployment's, and getting it wrong binds one party's data to another's tenant.
    # Parsed, not grepped: the prose above the function names the old expression on purpose,
    # and a check that cannot tell code from a comment teaches people to stop writing the
    # comment.
    indexes_the_list = any(
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "organizations"
        for node in ast.walk(ast.parse(script))
    )
    if indexes_the_list:
        failures.append("grounding: the tenant is still taken from organizations[N] rather than by alias")
    elif "TENANT_ALIAS" not in script:
        failures.append("grounding: the script does not read TENANT_ALIAS, so no tenant is named")
    else:
        print("  ok    grounding: the tenant is chosen by alias")

    # 3. The result policy, in an object the cloud collects. A Job is not collected.
    policy = None
    for document in render("conversational-analyst-grounding", GROUNDING_CASE):
        if document.get("kind") == "ConfigMap" and "model-policy" in (document.get("metadata") or {}).get("name", ""):
            policy = document
    if policy is None:
        failures.append(
            "grounding: no ConfigMap carries the result policy, so the thresholds are in the "
            "Job's env only and a verifier cannot read them out of the evidence snapshot"
        )
    else:
        missing = [
            key
            for key in ("LARGE_DATA_ROW_THRESHOLD", "LARGE_DATA_SIZE_THRESHOLD", "PREVIEW_ROW_COUNT", "CONTEXT_WINDOW_SIZE")
            if key not in (policy.get("data") or {})
        ]
        if missing:
            failures.append(f"grounding: the attested policy does not carry {', '.join(missing)}")
        else:
            print(f"  ok    grounding: the result policy is attested in ConfigMap/{policy['metadata']['name']}")
    return failures


def job_name(extra: list[str]) -> str:
    """The grounding Job's name as the chart renders it for these values."""
    manifests = subprocess.run(
        ["helm", "template", "analyst", "charts/conversational-analyst-grounding",
         "--namespace", "conversational-analyst", "--values", GROUNDING_CASE, *extra],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    for document in yaml.safe_load_all(manifests):
        if document and document.get("kind") == "Job":
            return document["metadata"]["name"]
    raise AssertionError("the grounding chart rendered no Job")


def check_grounding_job_reruns_when_its_policy_moves() -> list[str]:
    """A Job's pod template is immutable, so the name is the only thing that can re-run it.

    Every number the Job writes into the model registry therefore has to be in its name, or a
    reconfigure that changes one leaves the superseded value in the registry and nothing says
    so. `thresholds` alone was not the whole policy (CTO review of #94). The access key is the
    one value that must NOT move it: a regenerated credential is not a reason to re-ground.
    """
    failures = []
    baseline = job_name([])
    for setting, description in [
        ("model.thresholds.largeDataRowThreshold=7", "a row threshold"),
        ("model.thresholds.largeDataSizeThreshold=7", "a size threshold"),
        ("model.thresholds.previewRowCount=7", "the preview row count"),
        ("model.contextWindowSize=131072", "the context window"),
        ("model.memoryTokenBudget=7", "the memory budget"),
        ("model.summarizeTokenThreshold=7", "the summarisation threshold"),
    ]:
        if job_name([f"--set={setting}"]) == baseline:
            failures.append(f"grounding: changing {description} does not re-run the Job ({setting})")
    if not failures:
        print("  ok    grounding: every policy number moves the Job's name")

    if job_name(["--set=model.apiKey=ANOTHER-CREDENTIAL"]) != baseline:
        failures.append("grounding: a regenerated access key re-runs the Job, which is not a reason to re-ground")
    else:
        print("  ok    grounding: a regenerated access key does not")
    return failures


def check_grounding_sends_the_enum_as_the_api_spells_it() -> list[str]:
    """`LlmProvider` is an enum and its values are upper case; `SchemaAnnotationTarget`'s are not.

    The lower-case provider was refused with «Value "openai" does not exist in "LlmProvider"
    enum», which is where the Job stopped once its pagination was right (SUP-711). Both
    spellings are asserted, because the obvious tidy-up — one case for all of them — breaks
    the other half.
    """
    script = (ROOT / "charts/conversational-analyst-grounding/files/ground.py").read_text()
    failures = []
    if re.search(r'"provider":\s*"openai"', script) or 'PROVIDER = "openai"' in script:
        failures.append('grounding: the model provider is sent as "openai"; the LlmProvider enum takes "OPENAI"')
    elif 'PROVIDER = "OPENAI"' not in script:
        failures.append("grounding: the model provider is not the OPENAI enum value")
    else:
        print("  ok    grounding: the provider is sent as the OPENAI enum value")

    for value in ('"target": "table"', '"target": "column"'):
        if value not in script:
            failures.append(f"grounding: {value} is gone; SchemaAnnotationTarget's values are lower case")
    return failures


def main() -> int:
    failures = []
    failures += check_garage_rpc_secret()
    failures += check_keycloak_sizing()
    failures += check_the_listing_leaves_the_heap_configmap_alone()
    failures += check_config_init_reads_a_configmap_mount()
    failures += check_grounding_speaks_the_api_1_17_talks()
    failures += check_grounding_job_reruns_when_its_policy_moves()
    failures += check_grounding_sends_the_enum_as_the_api_spells_it()
    for failure in failures:
        print(f"  FAIL  {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
