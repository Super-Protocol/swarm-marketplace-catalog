#!/usr/bin/env python3
"""Ready only when every engine in this container is.

A pod has one readiness answer to give, and the HTTP probe Kubernetes offers can
ask about one port. Several engines share this pod behind one Service and one
Ingress, so "ready" has to mean all of them: a pod marked Ready while the second
model is still loading publishes a path that answers 502 for minutes, and a
liveness probe that watches one port never notices the other engine wedging.

`/health` is outside vLLM's authentication middleware, which is what makes it
usable as a probe at all — and it answers only once the model is loaded.

stdlib only, and no dependency on the chart: the ports come from the same
attested engines.json the supervisor is handed.
"""
import json
import os
import sys
import urllib.error
import urllib.request

TIMEOUT = 5


def main() -> int:
    manifest = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
        "ENGINES_MANIFEST", "/opt/engines/engines.json")
    with open(manifest) as handle:
        engines = json.load(handle)["engines"]
    if not engines:
        print(f"{manifest} names no engines")
        return 1

    unready = []
    for engine in engines:
        url = f"http://127.0.0.1:{engine['port']}/health"
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
                if response.status != 200:
                    unready.append(f"{engine['name']}: HTTP {response.status}")
        except (urllib.error.URLError, OSError, TimeoutError) as err:
            unready.append(f"{engine['name']}: {err}")
    if unready:
        print("; ".join(unready))
        return 1
    print(f"{len(engines)} engine(s) ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
