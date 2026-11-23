#!/usr/bin/env python3
"""Write the chart's `-app` ConfigMap out as files, from a render on stdin.

    helm template ... | unpack-app.py <directory>

What a deployment runs is the ConfigMap, not `charts/confidential-rag/files/`.
Taking the code from the render is what makes this smoke run exercise the thing
the cloud would digest and sign — and makes a template that stopped shipping one
of those files fail here rather than in a cluster.
"""

import pathlib
import sys

import yaml

target = pathlib.Path(sys.argv[1])
for document in yaml.safe_load_all(sys.stdin):
    if (
        document
        and document.get("kind") == "ConfigMap"
        and document["metadata"]["name"].endswith("-app")
    ):
        for name, body in document["data"].items():
            (target / name).write_text(body)
        print(" ".join(sorted(document["data"])))
        break
else:
    raise SystemExit("no -app ConfigMap in the render")
