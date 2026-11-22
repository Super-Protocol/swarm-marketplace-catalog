#!/usr/bin/env bash
#
# What runs instead of the image's own command, which starts JupyterLab with
# `--NotebookApp.token=''` — unauthenticated, correct for a laptop and wrong for
# a public hostname with a GPU behind it.
#
# Three steps, in this order and for these reasons:
#
#   1. install the attested environment. The lock names every wheel by sha256
#      and pip refuses anything else, so what the notebooks import is what the
#      deployment's evidence says it is. ~30 s on a warm link.
#   2. seed the workspace. The notebooks and `sc_workspace.py` are copied into
#      the volume only when they are not already there: the tenant edits these
#      files, and a restart that overwrote their work would be a data loss. The
#      pristine copies stay readable under /opt/sc for comparison.
#   3. exec JupyterLab. `exec`, so the server is PID 1's child and a SIGTERM
#      reaches it rather than this script.
set -euo pipefail

echo "[bootstrap] installing the attested environment"
# --no-cache-dir: the cache would be written into the image layer and thrown
# away with the pod anyway, and a 1 GiB wheel cache is a GiB of the container's
# memory limit spent on page cache.
pip install --no-cache-dir --require-hashes --disable-pip-version-check \
  -r /opt/sc/requirements.lock

echo "[bootstrap] seeding /workspace"
mkdir -p /workspace/notebooks /workspace/data /workspace/derived
# `cp -n`: never clobber. Only files the tenant does not have yet are added, so
# a new notebook in a chart version arrives without taking anybody's edits with
# it.
cp -n /opt/sc-notebooks/*.ipynb /workspace/notebooks/ 2>/dev/null || true
cp -n /opt/sc-notebooks/*.md    /workspace/notebooks/ 2>/dev/null || true
cp -n /opt/sc-notebooks/LICENSE /workspace/notebooks/ 2>/dev/null || true
cp -n /opt/sc/sc_workspace.py   /workspace/notebooks/ 2>/dev/null || true

python3 - <<'PY'
import sys
sys.path.insert(0, "/opt/sc")
import sc_workspace as scw
print("[bootstrap]", scw.describe_binding())
PY

echo "[bootstrap] starting JupyterLab"
# No hostname and no token on this command line, deliberately. The token comes
# from the environment (`JUPYTER_TOKEN`, which jupyter_server reads by itself)
# and the hostname is not needed at all: with `ip` not a loopback address
# jupyter_server allows remote access, and its websocket origin check compares
# the browser's Origin against the Host the ingress passes through — both are
# the public name, so same-origin holds without naming it.
#
# Anything that named the hostname here would be a hostname in an attested
# object, and the evidence digest would stop being a property of the version.
exec jupyter lab \
  --ServerApp.ip=0.0.0.0 \
  --ServerApp.port=8888 \
  --ServerApp.open_browser=False \
  --ServerApp.root_dir=/workspace \
  --ServerApp.default_url=/lab/tree/notebooks \
  --ServerApp.trust_xheaders=True \
  --ServerApp.terminado_settings="{'shell_command': ['/bin/bash']}"
