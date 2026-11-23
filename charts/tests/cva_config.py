#!/usr/bin/env python3
"""What the agents may reach is a property of one attested ConfigMap, so it is checked.

    charts/tests/cva_config.py

This listing's confidentiality claim is not about where the pods run. It is this: the tenant's
root filesystem, image name, package inventory and reports stay inside the cluster space, and the
only thing that crosses the boundary is a CVE identifier in a URL path to one host. The pipeline's
configuration is where that is true or false, and the chart renders it into a ConfigMap the cloud
digests — so a verifier reads it in the signed evidence rather than taking an image digest on
trust.

A golden diff would notice a change to that document but says nothing about what the change
means. These are the properties, each one a way the claim could stop being true while everything
still rendered and deployed:

1. **No web-research tool exists in the configuration.** `web_search` is Tavily — a third-party
   API that would receive free-text queries naming the tenant's packages, versions and image —
   and `web_fetch` retrieves an arbitrary URL, which is unbounded egress by construction. Neither
   may be a function, and neither may appear in any agent's tool list.
2. **`osv_lookup` is the only networked tool, and the researcher's only tool.** It fetches
   `/v1/vulns/<CVE-ID>` from one host and compares the package and version it is given against
   the response locally.
3. **The public CVE record is not consulted.** `validate_cve_exists: false`; upstream's default
   reaches `cveawg.mitre.org`, a second external host.
4. **Every model is an OpenAI-compatible endpoint inside this cluster space.** A single
   `_type: nim` left behind, or a `base_url` that is not the composed engine, sends every prompt
   — which carries the contents of the tenant's container — to a hosted endpoint.
5. **Every `context_window_tokens` equals the engine's own window, and every output ceiling is
   below it.** Left null, `model_metadata` discovers a limit from the endpoint and falls back to
   131072, which is twice this engine's window: prompts are then built, sent and refused.
6. **The reports are written to the volume**, by an absolute path — a relative `output_dir`
   resolves against the working directory, inside the image, where a restart loses every report.
7. **The stage cache namespace names the model.** A model or prompt change must not be able to
   serve a report produced under the previous one.
8. **No credential is in the document.** It is a ConfigMap: everything in it is published in the
   snapshot in clear.
"""

from __future__ import annotations

import subprocess
import sys

import yaml

CHART = "charts/confidential-vulnerability-analysis"
VALUES = "charts/tests/cases/vuln-analysis-stack.yaml"
# Upstream's two web tools. Named rather than detected, because the point is that these exact
# functions are absent: a tool that reaches the web under another name would be a different
# review, and a rename of these would be a change somebody made deliberately.
WEB_TOOLS = {"web_search", "web_fetch"}
# The engine the listing composes, as the configuration must name it: a Service name in this
# cluster space, no namespace in it.
ENGINE_URL = "http://swarm-model-server:8000/v1"
EXPECTED_WINDOW = 65536
CREDENTIAL_MARKERS = ("FOR-THE-GOLDEN", "api_key", "apiKey", "password")


def render() -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "va", CHART, "--namespace", "vuln-analysis",
         "--skip-tests", "--values", VALUES],
        capture_output=True, text=True, check=True,
    ).stdout
    return [doc for doc in yaml.safe_load_all(out) if doc]


def main() -> int:
    docs = render()
    problems: list[str] = []

    config_maps = {doc["metadata"]["name"]: doc for doc in docs if doc["kind"] == "ConfigMap"}
    document = config_maps.get("confidential-vuln-analysis-config")
    if document is None:
        print("  FAIL  the pipeline's configuration is not rendered into a ConfigMap at all")
        return 1

    raw = document["data"]["config.yml"]
    config = yaml.safe_load(raw)

    functions = config.get("functions") or {}
    present_web = WEB_TOOLS & set(functions)
    if present_web:
        problems.append(
            f"{sorted(present_web)} is configured as a function — this deployment's whole claim "
            "is that the tenant's package inventory is not used to query a public service"
        )

    tool_lists = {
        "cve_researcher": functions.get("cve_researcher", {}).get("tool_names") or [],
        "container_analyzer.web": functions.get("container_analyzer", {}).get("web_tool_names") or [],
        "container_analyzer.fs": functions.get("container_analyzer", {}).get("fs_tool_names") or [],
        "exploitability_analyzer": functions.get("exploitability_analyzer", {}).get("tool_names") or [],
    }
    for where, tools in tool_lists.items():
        leaked = WEB_TOOLS & set(tools)
        if leaked:
            problems.append(f"{where} is given {sorted(leaked)}")

    if tool_lists["container_analyzer.web"]:
        problems.append(
            "container_analyzer.web_tool_names is not empty: phase 1 researches a public image "
            "on the web, and this deployment has no web tool and never pulled the image"
        )

    if "osv_lookup" not in functions:
        problems.append("osv_lookup is not configured; the CVE researcher would have no tool at "
                        "all and every finding would come back `unknown` / `uncertain`")
    if tool_lists["cve_researcher"] != ["osv_lookup"]:
        problems.append(
            f"the CVE researcher's tools are {tool_lists['cve_researcher']}, not exactly "
            "['osv_lookup']"
        )

    researcher = functions.get("cve_researcher", {})
    if researcher.get("validate_cve_exists") is not False:
        problems.append(
            "validate_cve_exists is not false: each identifier would be checked against "
            "cveawg.mitre.org, which is a second external host"
        )

    for name, llm in (config.get("llms") or {}).items():
        if llm.get("_type") != "openai":
            problems.append(f"llm {name} is `_type: {llm.get('_type')}`, not `openai`")
        if llm.get("base_url") != ENGINE_URL:
            problems.append(
                f"llm {name} answers at {llm.get('base_url')}, not the engine in this cluster "
                f"space ({ENGINE_URL})"
            )
        ceiling = llm.get("max_tokens")
        if ceiling is not None and ceiling >= EXPECTED_WINDOW:
            problems.append(
                f"llm {name} may generate {ceiling} tokens, which is not below the engine's "
                f"{EXPECTED_WINDOW}-token window — the engine refuses such a request outright"
            )

    for name in ("container_analyzer", "exploitability_analyzer", "vex_categorizer"):
        window = functions.get(name, {}).get("context_window_tokens")
        if window != EXPECTED_WINDOW:
            problems.append(
                f"{name}.context_window_tokens is {window!r}, not {EXPECTED_WINDOW}: null is "
                "resolved from the endpoint and falls back to 131072, twice this engine's window"
            )

    workflow = config.get("workflow") or {}
    output_dir = str(workflow.get("output_dir", ""))
    if not output_dir.startswith("/data"):
        problems.append(
            f"output_dir is {output_dir!r}: a relative path resolves against the working "
            "directory, inside the image, where a restart loses every report"
        )
    model = (config.get("llms") or {}).get("vex_llm", {}).get("model_name", "")
    if model and model not in str(workflow.get("cache_namespace", "")):
        problems.append(
            f"the stage cache namespace {workflow.get('cache_namespace')!r} does not name the "
            f"model ({model}): a model swap would serve reports produced by the previous one"
        )

    lowered = raw.lower()
    for marker in CREDENTIAL_MARKERS:
        if marker.lower() in lowered:
            problems.append(
                f"the configuration mentions {marker!r}: a ConfigMap is published in the "
                "evidence snapshot in clear, so a credential belongs in the Secret"
            )

    if problems:
        print("  FAIL  the attested configuration does not say what the listing says it says")
        for problem in problems:
            print(f"          {problem}")
        return 1

    print(f"  ok    no web tool, osv_lookup only, mitre validation off, {len(config['llms'])} "
          f"openai endpoints inside the space, {EXPECTED_WINDOW}-token windows, no credential")
    return 0


if __name__ == "__main__":
    sys.exit(main())
