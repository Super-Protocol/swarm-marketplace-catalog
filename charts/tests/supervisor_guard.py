#!/usr/bin/env python3
"""The supervisor's promises, and the readiness probe beside it, exercised rather
than read.

`charts/swarm-model-server/files/run-engines.py` is what makes two models on one
card a deployment rather than a pair of processes that happen to share a
container: Kubernetes sees one container, so everything Kubernetes would have
done for a second one — notice it died, stop it on a delete, keep its logs apart
— has to be done here.

None of that is visible to a golden render, and none of it is visible to a
readiness probe either: the failure each case below describes is a pod whose
restart count stays at zero while half the endpoint is gone.

`engine-health.py` is checked here too, against real sockets: it is the only
thing that makes "Ready" mean *both* models, and an HTTP probe cannot ask about
two ports.

Fake engines, so nothing here needs a GPU, an image or the network: an engine is
whatever `engines.json` says it is, which is the property that makes the
supervisor testable at all.
"""

from __future__ import annotations

import json
import os
import pathlib
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
SUPERVISOR = ROOT / "charts" / "swarm-model-server" / "files" / "run-engines.py"
HEALTH = ROOT / "charts" / "swarm-model-server" / "files" / "engine-health.py"

# A child that answers to SIGTERM, prints a line and otherwise sits there.
SERVER = (
    "import signal, sys, time\n"
    "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
    "print('listening', flush=True)\n"
    "time.sleep(600)\n"
)


def manifest(work: pathlib.Path, engines: list[dict]) -> pathlib.Path:
    path = work / "engines.json"
    path.write_text(json.dumps({"engines": engines}))
    return path


def engine(name: str, script: str, port: int = 8000, cache: str | None = None) -> dict:
    spec = {
        "name": name,
        "port": port,
        "command": [sys.executable, "-c", script],
    }
    if cache:
        spec["env"] = {
            "VLLM_CACHE_ROOT": f"{cache}/vllm",
            "TRITON_CACHE_DIR": f"{cache}/triton",
            "TORCHINDUCTOR_CACHE_DIR": f"{cache}/inductor",
        }
    return spec


def run(path: pathlib.Path, *, send: int | None = None, after: float = 1.5,
        timeout: float = 60) -> tuple[int, str]:
    process = subprocess.Popen(
        [sys.executable, str(SUPERVISOR), str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if send is not None:
        time.sleep(after)
        process.send_signal(send)
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate()
        return 124, output
    return process.returncode, output


def free_ports(count: int) -> list[int]:
    """Ports nothing else is on, so the check does not depend on 8000 being free."""
    sockets = [socket.socket() for _ in range(count)]
    for sock in sockets:
        sock.bind(("127.0.0.1", 0))
    ports = [sock.getsockname()[1] for sock in sockets]
    for sock in sockets:
        sock.close()
    return ports


def wait_for(ports: list[int], timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    for port in ports:
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.05)


def health(path: pathlib.Path, *, capture: bool = False):
    result = subprocess.run([sys.executable, str(HEALTH), str(path)],
                            capture_output=True, text=True, timeout=60)
    output = (result.stdout + result.stderr).strip()
    return (result.returncode, output) if capture else result.returncode


def main() -> int:
    failures: list[str] = []

    def check(label: str, condition: bool, detail: str = "") -> None:
        if condition:
            print(f"  ok    {label}")
        else:
            failures.append(f"{label}{': ' + detail if detail else ''}")

    with tempfile.TemporaryDirectory() as raw:
        work = pathlib.Path(raw)

        # 1. One engine failing takes the container down with that engine's exit
        #    status, and the surviving engine is stopped rather than left serving
        #    half an endpoint.
        path = manifest(work, [
            engine("dies", "import sys, time; time.sleep(0.5); sys.exit(9)"),
            engine("lives", SERVER, port=8001),
        ])
        status, output = run(path)
        check("an engine that exits non-zero takes the container down with its status",
              status == 9, f"exit {status}\n{output}")
        check("the other engine is stopped rather than left serving half an endpoint",
              "stopping the others" in output, output)

        # 2. A *clean* exit is still an engine that stopped serving: exiting 0
        #    here would hand Kubernetes a container that finished successfully,
        #    and an operator a pod whose status says so next to a model that is
        #    gone.
        path = manifest(work, [
            engine("quits", "import sys, time; time.sleep(0.5); sys.exit(0)"),
            engine("lives", SERVER, port=8001),
        ])
        status, output = run(path)
        check("an engine that exits cleanly is still a failure", status != 0, f"exit {status}")

        # 3. SIGTERM reaches every child and is not a failure. Without the
        #    forwarding a delete waits out the grace period and then SIGKILLs an
        #    engine mid-request; without the exit status every rollout logs a
        #    crash.
        path = manifest(work, [
            engine("one", SERVER),
            engine("two", SERVER, port=8001),
        ])
        status, output = run(path, send=signal.SIGTERM)
        check("SIGTERM is forwarded to every engine and exits 0", status == 0, f"exit {status}")
        check("the forwarding says so in the log", "forwarding it to every engine" in output,
              output)

        # 4. Every line says which engine wrote it. Two vLLM servers interleaving
        #    unlabelled tracebacks is the log of a deployment nobody can debug.
        check("each engine's output carries its name",
              "[one] listening" in output and "[two] listening" in output, output)

        # 5. An engine that cannot be started is reported as that, with its name,
        #    rather than as a traceback from the supervisor.
        path = manifest(work, [{"name": "missing", "port": 8000,
                                "command": [str(work / "not-an-engine")]}])
        status, output = run(path)
        check("an engine that cannot be started names itself and fails",
              status != 0 and "missing could not be started" in output, f"exit {status}\n{output}")

        # 6. The per-engine cache directories are created. vLLM does not create
        #    its own cache parents, and two engines compiling into one directory
        #    is a race whose loser recompiles on every restart.
        cache = work / "cache" / "solo"
        path = manifest(work, [engine("solo", "import sys; sys.exit(0)", cache=str(cache))])
        run(path)
        check("each engine's caches are separated and created",
              (cache / "vllm").is_dir() and (cache / "triton").is_dir()
              and (cache / "inductor").is_dir(), str(sorted(os.listdir(cache)) if cache.exists() else cache))

        # 7. Ready means every engine. One of the two ports answering is the
        #    pod that publishes a path which 502s for minutes.
        responder = (
            "import http.server, sys\n"
            "class H(http.server.BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n"
            "        self.send_response(200 if self.path == '/health' else 404)\n"
            "        self.end_headers()\n"
            "    def log_message(self, *a): pass\n"
            "http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()\n"
        )
        ports = free_ports(2)
        children = [subprocess.Popen([sys.executable, "-c", responder, str(port)])
                    for port in ports]
        try:
            wait_for(ports)
            both = manifest(work, [engine("one", SERVER, port=ports[0]),
                                   engine("two", SERVER, port=ports[1])])
            check("the probe is ready when every engine answers",
                  health(both) == 0)
            children[1].terminate()
            children[1].wait()
            status, output = health(both, capture=True)
            check("the probe is unready when one engine does not, and names it",
                  status != 0 and "two:" in output, f"exit {status}: {output}")
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
                    child.wait()

    for message in failures:
        print(f"  FAIL  {message}")
    if failures:
        return 1
    print("the supervisor keeps its promises and the probe answers for every engine")
    return 0


if __name__ == "__main__":
    sys.exit(main())
