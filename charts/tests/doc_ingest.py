#!/usr/bin/env python3
"""
What `swarm-doc-ingest` has to keep true, checked against the render.

Documents are the sensitive asset in a RAG listing, so the properties this
component is built for are the ones a reviewer cannot verify by reading values:

  * **No model is fetched at request time.** `HF_HUB_OFFLINE` is `1` in the
    attested ConfigMap rather than only a default inside the image, and the
    service refuses to boot without it.
  * **No hostname in any object.** With none, two deployments of one version
    differ in the namespace alone, which the platform drops from the evidence
    snapshot — so a listing composing this component declares no exclusion for
    it (marketplace traps 6 and 8).
  * **The key never leaves a Secret.** Not an env value, not an argument: a
    command line is rendered into the manifests and published in the evidence
    snapshot in clear (trap 4).
  * **Every key in the env ConfigMap is a legal environment-variable name.**
    `envFrom` turns each one into a variable, and `extraction-notes.md` is not a
    legal name — the kubelet skips it and files an
    `InvalidEnvironmentVariableNames` event on every pod start. That is why the
    prose lives in a second ConfigMap, and this is the check that keeps it there.
  * **`maxUploadBytes` renders as an integer.** Helm reads a large YAML integer
    as a float: 268435456 came out as `"2.68435456e+08"`, which the service
    parses with `int()` and dies on at boot. A golden caught it once; this keeps
    it caught.
  * **`OMP_NUM_THREADS` is the CPU quota in cores, not its digits.**
    `resources.cpu` is a Kubernetes quantity, so `1500m` is one and a half
    cores; stripping the `m` read it as 1500 threads inside a cgroup that can
    run one and a half, which is the slowest extraction this chart can produce
    (SUP-882). Derived as the core count rounded up, never below 1.
  * **Only the baked layout preset renders.** The image carries
    `docling-layout-heron` alone and `HF_HUB_OFFLINE=1`, so a pod asked for an
    egret preset can never start. There is no acknowledgement flag, because
    there was nothing an acknowledgement could reach (SUP-882).

    charts/tests/doc_ingest.py
"""

from __future__ import annotations

import re
import subprocess
import sys

import yaml

CHART = "charts/swarm-doc-ingest"
#: The digest `values.yaml` pins. Read from the chart rather than written here,
#: so moving the pin does not silently leave this test asserting the old bytes.
def _pinned_digest() -> str:
    values = yaml.safe_load(open(f"{CHART}/values.yaml"))
    return values["image"]["digest"]


DIGEST = _pinned_digest()
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

failures: list[str] = []


def check(ok: bool, message: str) -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {message}")
    if not ok:
        failures.append(message)


def render(*sets: str, namespace: str = "doc-ingest",
           release: str = "di", case: str | None = None) -> str:
    cmd = ["helm", "template", release, CHART, "--skip-tests",
           "--namespace", namespace]
    if case:
        cmd += ["--values", f"charts/tests/cases/{case}.yaml"]
    for s in sets:
        cmd += ["--set", s]
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(done.stderr.strip())
    return done.stdout


def refuses(*sets: str, case: str | None = None) -> str:
    """The render must fail. Returns stderr so the caller can check the reason
    is the one it meant rather than an unrelated template error."""
    try:
        render(*sets, case=case)
    except AssertionError as exc:
        return str(exc)
    return ""


def docs(rendered: str) -> list[dict]:
    return [d for d in yaml.safe_load_all(rendered) if d]


def by_kind(objects: list[dict], kind: str) -> list[dict]:
    return [d for d in objects if d.get("kind") == kind]


def main() -> int:
    base = render(case="doc-ingest-component")
    objects = docs(base)

    print("\nthe component shape")
    kinds = sorted({d["kind"] for d in objects})
    check(kinds == ["ConfigMap", "ConfigMap", "Deployment", "Secret", "Service"]
          or sorted(kinds) == ["ConfigMap", "Deployment", "Secret", "Service"],
          f"renders only ConfigMap, Deployment, Secret and Service — got {kinds}")
    check(not by_kind(objects, "Ingress"),
          "no Ingress object")
    check("swarm-doc-ingest" not in [d["metadata"].get("name") for d in
                                     by_kind(objects, "Ingress")],
          "no Ingress named after the chart")
    service = by_kind(objects, "Service")[0]
    check(service["spec"]["type"] == "ClusterIP", "the Service is ClusterIP")
    check(service["metadata"]["name"] == "swarm-doc-ingest",
          "the Service name is fixed, not release-derived — a listing's outputs "
          "quote it")

    print("\nno model is fetched at request time")
    env_cm = next(d for d in by_kind(objects, "ConfigMap")
                  if d["metadata"]["name"] == "swarm-doc-ingest")
    check(env_cm["data"].get("HF_HUB_OFFLINE") == "1",
          "HF_HUB_OFFLINE is 1 in the attested ConfigMap")
    check(env_cm["data"].get("INGEST_OCR_ENGINE") == "rapidocr",
          "the OCR engine is named in the attested ConfigMap")

    print("\nevery env ConfigMap key is a legal variable name")
    container = (by_kind(objects, "Deployment")[0]["spec"]["template"]["spec"]
                 ["containers"][0])
    env_sources = [list(e.values())[0]["name"] for e in container["envFrom"]]
    for name in env_sources:
        cm = next(d for d in by_kind(objects, "ConfigMap")
                  if d["metadata"]["name"] == name)
        bad = [k for k in cm["data"] if not ENV_NAME.fullmatch(k)]
        check(not bad, f"{name}: every key is a legal variable name — bad: {bad}")
    notes = [d for d in by_kind(objects, "ConfigMap")
             if d["metadata"]["name"].endswith("-extraction-notes")]
    check(len(notes) == 1 and "swarm-doc-ingest-extraction-notes" not in env_sources,
          "the prose ConfigMap exists and is not consumed with envFrom")
    check("not transcribed" in notes[0]["data"]["extraction-notes.md"],
          "the attested notes say charts are located rather than transcribed")

    print("\nthe key never leaves a Secret")
    key = "TESTKEYtestkey0123456789abcdefAB"
    check(key not in yaml.safe_dump(container),
          "the key is in no container field — only behind secretKeyRef")
    check(not container.get("command") and not container.get("args"),
          "no command and no args: the image's own CMD is the memory decision")
    api_env = next(e for e in container["env"] if e["name"] == "INGEST_API_KEY")
    check("secretKeyRef" in api_env.get("valueFrom", {}),
          "INGEST_API_KEY arrives by secretKeyRef")
    secrets = by_kind(objects, "Secret")
    check(len(secrets) == 1 and secrets[0]["stringData"]["api-key"] == key,
          "the chart's own Secret carries it")

    print("\nthe numbers the quota is computed from")
    check(container["resources"]["requests"] == container["resources"]["limits"],
          "requests equal limits — a request below the limit lets the scheduler "
          "place a pod the quota cannot hold")
    check(container["resources"]["limits"] == {"cpu": "4", "memory": "4Gi"},
          "4 CPU and 4Gi, the measured footprint")
    check(env_cm["data"]["INGEST_MAX_UPLOAD_BYTES"] == "268435456",
          "maxUploadBytes is an integer, not Helm's float rendering of one")
    check(env_cm["data"]["OMP_NUM_THREADS"] == "4",
          "the thread count follows the pod's own CPU limit")

    print("\nthe work volume is not charged to the memory limit")
    volumes = (by_kind(objects, "Deployment")[0]["spec"]["template"]["spec"]
               ["volumes"])
    work = next(v for v in volumes if v["name"] == "work")
    check("medium" not in work["emptyDir"],
          "the upload volume is disk-backed — `medium: Memory` is charged to the "
          "container's memory limit")
    check(work["emptyDir"].get("sizeLimit") == "1Gi", "and it is bounded")

    print("\nthe render is a property of the version, not of the deployment")
    one = render("apiKey=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                 f"image.digest={DIGEST}", "image.tag=0.1.0",
                 namespace="first")
    two = render("apiKey=BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
                 f"image.digest={DIGEST}", "image.tag=0.1.0",
                 namespace="second")
    # The namespace reaches no object here, so the only difference two
    # deployments of one version can have is the key, which the platform lifts
    # into a sealed Secret before the snapshot is taken.
    diff = [(a, b) for a, b in zip(one.splitlines(), two.splitlines()) if a != b]
    check(len(diff) == 1 and "api-key" in diff[0][0],
          f"two deployments differ in the key alone — differing lines: {diff}")
    again = render("apiKey=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                   f"image.digest={DIGEST}", "image.tag=0.1.0",
                   namespace="first")
    check(again == one, "a second render of identical parameters is byte-identical")

    print("\nmisconfigurations are refused at render time")
    cases = [
        (("apiKey=x", "image.digest="), "pinned by digest",
         "an image with no digest"),
        ((f"image.digest={DIGEST}",), "not a trust boundary",
         "no key and no existingSecret"),
        ((f"image.digest={DIGEST}", "apiKey=x",
          "existingSecret=other"), "both set",
         "apiKey and existingSecret together"),
        ((f"image.digest={DIGEST}", "apiKey=x",
          "extraction.layoutPreset=layout_nonesuch"), "not a Docling preset",
         "a layout preset that does not exist"),
        ((f"image.digest={DIGEST}", "apiKey=x",
          "extraction.layoutPreset=layout_egret_large"), "would never start",
         "a layout preset the image does not bake"),
        # The flag that used to let this through is gone. It could only ever
        # reach a pod that cannot start, so there is no acknowledgement to make.
        ((f"image.digest={DIGEST}", "apiKey=x",
          "extraction.layoutPreset=layout_egret_large",
          "extraction.acknowledgeUnmeasuredLayout=true"), "would never start",
         "an unbaked preset with the old acknowledgement set"),
        ((f"image.digest={DIGEST}", "apiKey=x",
          "extraction.tableMode=fastest"), "one of fast, accurate",
         "an unknown table mode"),
        ((f"image.digest={DIGEST}", "apiKey=x",
          "extraction.ocrEngine=easyocr"), "bakes RapidOCR only",
         "an OCR engine the image does not bake"),
        ((f"image.digest={DIGEST}", "apiKey=x", "replicaCount=3",
          "workVolume.sizeLimit="), "fills the node",
         "more than one replica with an unbounded work volume"),
    ]
    for sets, expected, description in cases:
        message = refuses(*sets)
        check(bool(message) and expected in message,
              f"refused: {description}")

    print("\nthe baked preset is the only one that renders")
    try:
        render(f"image.digest={DIGEST}", "apiKey=x",
               "extraction.layoutPreset=layout_heron_default")
        check(True, "the baked preset renders")
    except AssertionError as exc:
        check(False, f"the baked preset should render: {exc}")

    print("\nthe thread count is the CPU quota in cores, not its digits")
    # `resources.cpu` is a Kubernetes quantity, so `1500m`, `1.5` and `4` are
    # three legal ways to write one. Stripping the `m` read `1500m` as 1500
    # threads inside a one-and-a-half-core cgroup — the slowest extraction this
    # chart can produce, and the opposite of what the value is for (SUP-882).
    # Rounded up, because 0 is not a thread count.
    for cpu, expected in [("4", "4"), ("8", "8"), ("1500m", "2"), ("1.5", "2"),
                          ("500m", "1"), ("100m", "1"), ("2500m", "3"),
                          ("0.25", "1")]:
        rendered = render(f"image.digest={DIGEST}", "apiKey=x",
                          f"resources.cpu={cpu}")
        cm = next(d for d in by_kind(docs(rendered), "ConfigMap")
                  if d["metadata"]["name"] == "swarm-doc-ingest")
        got = cm["data"]["OMP_NUM_THREADS"]
        check(got == expected,
              f"cpu {cpu} gives OMP_NUM_THREADS {expected} — got {got}")
    override = render(f"image.digest={DIGEST}", "apiKey=x",
                      "resources.cpu=500m", "ompNumThreads=2")
    cm = next(d for d in by_kind(docs(override), "ConfigMap")
              if d["metadata"]["name"] == "swarm-doc-ingest")
    check(cm["data"]["OMP_NUM_THREADS"] == "2",
          "an explicit ompNumThreads still wins over the derivation")
    # And through a values file rather than `--set`, which is how a listing
    # patches it: Helm reads the YAML number itself and `1500m` stays a string
    # while `1.5` arrives as a float.
    fractional = docs(render(case="doc-ingest-millicores"))
    cm = next(d for d in by_kind(fractional, "ConfigMap")
              if d["metadata"]["name"] == "swarm-doc-ingest")
    fractional_container = (by_kind(fractional, "Deployment")[0]["spec"]
                            ["template"]["spec"]["containers"][0])
    check(fractional_container["resources"]["limits"]["cpu"] == "1500m"
          and cm["data"]["OMP_NUM_THREADS"] == "2",
          "the millicore case renders a 1500m quota and 2 threads")

    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print(f"all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
