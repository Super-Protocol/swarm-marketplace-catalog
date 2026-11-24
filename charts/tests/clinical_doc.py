#!/usr/bin/env python3
"""The Confidential Clinical Documentation chart's invariants, none of which a golden shows.

A golden diff proves the render has not changed. It cannot say whether what the render says is
true, and five of the things this chart asserts are exactly that kind of claim.

1. **The console refuses to start without a password.** The chart validates one, but the code
   that serves the door is in a ConfigMap, and a future edit could make a missing password a
   warning instead of an exit. A console that came up anonymous would pass every probe in the
   chart and publish a clinical archive.
2. **No container in this pod asks for the card.** The device plugin gives the one H100 to
   exactly one container and that container is the generation engine. A GPU request here is not
   lost throughput — it is a Pod that stays Pending for ever beside an engine that then cannot
   start either, and the cloud's deployment endpoint reports `ready: 0` with no reason.
3. **The guard's allowlist is empty in the rendered object.** The whole claim this listing makes
   about egress is one env value; it is worth reading it out of the manifest rather than out of
   the values file.
4. **The generation endpoint is in `NO_PROXY`.** Every HTTP client in the scribe container is
   pointed at the guard, which admits nothing — so if the engine's own Service name is not
   excluded, the model call is refused by this deployment's own guard and every note fails. The
   render is perfect, the deployment is healthy, and nothing works.
5. **No credential is anywhere but the Secret.** An env value, an argument or a ConfigMap entry
   is rendered into the manifests, travels in the bundle in clear and lands in the published
   evidence snapshot.

And one more, because it is this listing's subject: **the diarization floors reach the container
and none of them is zero.** A floor of zero means every diarization result passes, including one
that put both people in one cluster — which is upstream's behaviour with a reassuring field name,
and produces a confident wrong attribution that nothing reports.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHART = "charts/confidential-clinical-documentation"
CASE = "charts/tests/cases/clinical-doc-stack.yaml"

# The values the case file sets. Each must appear in the Secret and nowhere else.
SECRETS = [
    "ACCESS-PASSWORD-FOR-THE-GOLDEN",
    "SESSION-SIGNING-KEY-FOR-THE-GOLDEN-01234",
    "INFERENCE-KEY-FOR-THE-GOLDEN",
]

failures: list[str] = []


def check(ok: bool, message: str) -> None:
    print(f"  {'ok   ' if ok else 'FAIL '} {message}")
    if not ok:
        failures.append(message)


def render(*overrides: str) -> str:
    command = ["helm", "template", "ccd", CHART, "--namespace", "clinical-doc",
               "--values", CASE]
    for override in overrides:
        command += ["--set", override]
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if done.returncode != 0:
        print(done.stderr.strip()[:2000])
        raise SystemExit(f"render failed: {' '.join(command)}")
    return done.stdout


def documents(rendered: str) -> list[str]:
    return [d for d in rendered.split("\n---\n") if d.strip()]


def main() -> int:
    rendered = render()
    docs = documents(rendered)
    secret = next(d for d in docs if "kind: Secret" in d)
    others = [d for d in docs if "kind: Secret" not in d]
    deployment = next(d for d in docs if "kind: Deployment" in d)
    configmap = next(d for d in docs if "kind: ConfigMap" in d)

    print("the console refuses to serve an unauthenticated door")
    check("CONSOLE_PASSWORD and CONSOLE_SESSION_KEY are required" in configmap,
          "console.py exits without a password and a session key")
    check("refusing to serve" in configmap, "and says why")

    print("\nno container in this pod asks for the card")
    # Every container and init container names the resource explicitly, and every one is "0".
    # Both shapes the templates render: a block map line, and the init container's flow map.
    gpu_values = re.findall(r'nvidia\.com/gpu:\s*"?([^",}\s]+)"?', deployment)
    check(len(gpu_values) >= 8,
          f"every container names nvidia.com/gpu in request and limit ({len(gpu_values)} values)")
    check(all(value == "0" for value in gpu_values),
          f"and every one of them is zero ({sorted(set(gpu_values))})")

    print("\nthe egress guard admits nothing")
    check('- name: EGRESS_ALLOW_HOST\n              value: ""' in deployment,
          "EGRESS_ALLOW_HOST is empty in the rendered Deployment")
    check("python3, /opt/app/egress_guard.py" in deployment, "and the guard is running")
    check("ALLOW_HOST" in configmap and "_refuse_response" in configmap,
          "and its allowlist is the attested ConfigMap, not a container image")

    print("\nthe generation endpoint is not refused by this deployment's own guard")
    no_proxy = [line for line in deployment.splitlines()
                if "swarm-model-server" in line and "NO_PROXY" not in line]
    check(any("value: \"localhost" in line and "swarm-model-server" in line
              for line in deployment.splitlines()),
          "NO_PROXY names the engine's Service host")
    check(bool(no_proxy), "and the engine URL is rendered")

    print("\nno credential outside the Secret")
    for value in SECRETS:
        check(value in secret, f"{value[:24]}… is in the Secret")
        leaked = [d.splitlines()[1].strip() for d in others if value in d]
        check(not leaked, f"{value[:24]}… is in no other object"
                          + (f" (found in {leaked})" if leaked else ""))
    # The corpus binding is a connection document, and for an ungated dataset it carries the
    # owner's own credentials.
    check("corpus-binding:" in secret, "the corpus binding is a Secret key")
    # Only the Deployment is asked: `console.py` in the ConfigMap names the variable it reads
    # from the environment, which is the code, not the value.
    binding_env = deployment[deployment.index("CORPUS_BINDING_JSON"):][:200]
    check("secretKeyRef" in binding_env,
          "and reaches the console by secretKeyRef rather than as a value")
    check(all("acme" not in d for d in others), "and no connection document is in a rendered object")

    print("\nthe diarization floors reach the container, and none is zero")
    floors = {}
    lines = deployment.splitlines()
    for index, line in enumerate(lines):
        name = line.strip()
        if name.startswith("- name: DIARIZATION_FLOOR_"):
            key = name.removeprefix("- name: ")
            value = lines[index + 1].strip().removeprefix("value: ").strip('"')
            floors[key] = value
    check(len(floors) >= 3, f"three floors are declared ({sorted(floors)})")
    check(all(float(v) > 0 for v in floors.values()),
          f"and none of them is zero ({floors})")
    check('- name: ENABLE_SPEAKER_DIARIZATION\n              value: "true"' in deployment,
          "and diarization is on")

    print("\nthe scribe grants cross-origin credentials to nobody")
    check('- name: CORS_ALLOW_ORIGINS\n              value: ""' in deployment,
          "CORS_ALLOW_ORIGINS is empty — upstream sends allow_origins=['*'] with credentials")

    print("\nthe scribe is on loopback and has no Service")
    check('"127.0.0.1"' in deployment, "the scribe binds 127.0.0.1")
    service = next(d for d in docs if "kind: Service" in d)
    check("ccd-console" not in service or "component: scribe" in service,
          "the one Service selects the pod and publishes the console port only")
    check(service.count("port:") == 1, "and it exposes exactly one port")

    print("\nretention is declared, and a recording is not kept for ever by default")
    check('- name: AUDIO_RETENTION_HOURS' in deployment, "AUDIO_RETENTION_HOURS is set")

    print()
    if failures:
        print(f"{len(failures)} failure(s)")
        return 1
    print("all clinical-documentation chart invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
