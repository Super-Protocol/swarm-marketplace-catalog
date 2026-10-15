#!/usr/bin/env python3
"""Run one vLLM server per engine inside the container that holds the card.

Kubernetes allocates a GPU to a container, not to a pod: two containers cannot
share one `nvidia.com/gpu: 1`, and the second one asking for it leaves the pod
Pending for ever. So engines that share a card share a container, and this is
what supervises them (SUP-332).

It chooses nothing. Every engine's name, port, environment and full command line
come from the `engines.json` the chart renders into the ConfigMap the cloud
digests and signs, so a deployment serving two models is as attested as one
serving one.

Four behaviours, each of them a failure seen in a cluster rather than a
preference:

  * **Any engine exiting takes the container down.** A pod that answers for one
    model and refuses connections for the other, with its restart count at zero,
    is the outage that reads as "the model is broken" for a week. The exit status
    is the engine's own, and a *clean* exit is still a failure here — an engine
    that has stopped serving has stopped serving.
  * **SIGTERM reaches every child, and is not a failure.** Without the first, a
    delete waits out the grace period and then SIGKILLs engines in the middle of
    a request; without the second, every ordinary rollout logs a crash.
  * **Every line of output says which engine wrote it.** Two vLLM servers
    interleaving unlabelled tracebacks is the log of a deployment nobody can
    debug.
  * **An engine that cannot be started is reported as that**, rather than as a
    traceback from the supervisor.

stdlib only, like the fetcher beside it.
"""
import json
import os
import signal
import subprocess
import sys
import threading

GRACE_SECONDS = 30

_stdout = threading.Lock()


def emit(line: str) -> None:
    with _stdout:
        sys.stdout.write(line.rstrip("\n") + "\n")
        sys.stdout.flush()


def log(message: str) -> None:
    emit(f"[engines] {message}")


def pump(name: str, stream) -> None:
    """Label every line an engine writes, so two logs stay two logs."""
    with stream:
        for line in stream:
            emit(f"[{name}] {line.rstrip()}")


class Engine:
    def __init__(self, spec: dict) -> None:
        self.name = spec["name"]
        self.port = spec.get("port")
        self.command = [str(part) for part in spec["command"]]
        self.env = dict(os.environ)
        self.env.update({key: str(value) for key, value in (spec.get("env") or {}).items()})
        self.process = None
        self.status = None

    def start(self) -> None:
        # The caches are per-engine (two engines compiling into one directory is
        # a race whose loser recompiles on every restart), and the engine does
        # not create its own parent directories.
        for variable in ("VLLM_CACHE_ROOT", "TRITON_CACHE_DIR", "TORCHINDUCTOR_CACHE_DIR"):
            if self.env.get(variable):
                os.makedirs(self.env[variable], exist_ok=True)
        log(f"starting {self.name} on :{self.port}: {' '.join(self.command)}")
        try:
            self.process = subprocess.Popen(
                self.command, env=self.env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
        except OSError as err:
            log(f"{self.name} could not be started: {err}")
            return
        threading.Thread(target=pump, args=(self.name, self.process.stdout), daemon=True).start()

    def send(self, number: int) -> None:
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.send_signal(number)
            except OSError as err:
                log(f"could not signal {self.name}: {err}")

    def reap(self, finished: list, exited: threading.Event) -> None:
        self.status = self.process.wait()
        # In exit order, not in values order: the engine this names is the one an
        # operator will go and read the logs of.
        finished.append(self)
        exited.set()


def main() -> int:
    manifest = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
        "ENGINES_MANIFEST", "/opt/engines/engines.json")
    with open(manifest) as handle:
        engines = [Engine(spec) for spec in json.load(handle)["engines"]]
    if not engines:
        log(f"{manifest} names no engines")
        return 1

    finished: list[Engine] = []
    exited = threading.Event()
    signalled = threading.Event()

    def forward(number, _frame):
        if not signalled.is_set():
            signalled.set()
            log(f"{signal.Signals(number).name}, forwarding it to every engine")
            for engine in engines:
                engine.send(number)

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)

    for engine in engines:
        engine.start()
        if engine.process is None:
            for started in engines:
                started.send(signal.SIGTERM)
            return 1
    for engine in engines:
        threading.Thread(target=engine.reap, args=(finished, exited), daemon=True).start()
    log(f"{len(engines)} engine(s) on one card: "
        + ", ".join(f"{engine.name} on :{engine.port}" for engine in engines))

    # Woken by the first engine to exit — including the exit a forwarded SIGTERM
    # causes. The timeout is what keeps the main thread available to run a signal
    # handler promptly.
    while not exited.wait(timeout=1):
        pass

    first = finished[0]
    if signalled.is_set():
        log(f"{first.name} exited with {first.status} while shutting down")
    else:
        log(f"{first.name} exited with {first.status}: stopping the others, because an endpoint "
            "that serves some of its models is not this deployment")
        for engine in engines:
            engine.send(signal.SIGTERM)

    for engine in engines:
        if engine.process is None:
            continue
        try:
            engine.process.wait(timeout=GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            log(f"{engine.name} did not stop within {GRACE_SECONDS}s, killing it")
            engine.process.kill()
            engine.process.wait()

    if signalled.is_set():
        return 0
    # A clean exit is still an engine that stopped serving.
    return first.status or 1


if __name__ == "__main__":
    sys.exit(main())
