#!/usr/bin/env bash
#
# What runs instead of the image's own command, which — this is the RAPIDS base
# image's default, inherited by the workbench — starts JupyterLab with
# `--NotebookApp.token=''`: unauthenticated, correct for a laptop and wrong for a
# public hostname with a GPU and a bank's transaction history behind it.
#
# Two steps, and no third: the environment is already in the image, pinned by
# sha256 and named in the deployment's evidence through the image digest, so
# nothing is installed here. That is the difference between this chart and the
# single-cell one, where the lock is installed at start-up.
#
#   1. seed the workspace. The notebooks are copied into the volume only when
#      they are not already there: the tenant edits these files, and a restart
#      that overwrote their work would be a data loss. The pristine copies stay
#      readable under /opt/tfm-notebooks for comparison.
#   2. exec JupyterLab. `exec`, so the server is PID 1's child and a SIGTERM
#      reaches it rather than this script.
set -euo pipefail

echo "[bootstrap] seeding /workspace"
mkdir -p /workspace/notebooks /workspace/data /workspace/derived
# `cp -n`: never clobber. Only files the tenant does not have yet are added, so
# a new notebook in a chart version arrives without taking anybody's edits with
# it.
cp -n /opt/tfm-notebooks/*.ipynb /workspace/notebooks/ 2>/dev/null || true
cp -n /opt/tfm-notebooks/*.md    /workspace/notebooks/ 2>/dev/null || true
cp -n /opt/tfm-notebooks/LICENSE /workspace/notebooks/ 2>/dev/null || true
# `tfm_workspace.py` is deliberately NOT copied. It is imported from
# /opt/tfm-chart, which the chart mounts and PYTHONPATH names first, so a fix to
# the loader reaches an existing deployment when the pod rolls. Copied here
# under `cp -n` it never would: the volume's stale copy would shadow the chart's
# for the life of the deployment, while this script's own log line read the
# fresh one.

# What this deployment is bound to and what it was told the columns are, in the
# log, where somebody debugging a deployment will look first. Deliberately NOT
# fatal: a workbench whose loader cannot read its binding is still a usable
# JupyterLab a tenant can fix from inside, and `set -e` turning this into a
# CrashLoopBackOff is what it did on the first real deployment of the
# single-cell chart (2026-11-22).
python3 - <<'PY' || echo "[bootstrap] could not read the dataset binding; the workbench starts anyway"
import sys
sys.path.insert(0, "/opt/tfm-chart")
import tfm_workspace as tfm
print("[bootstrap]", tfm.describe_binding())
print("[bootstrap]", tfm.describe_checkpoint())
for line in tfm.describe_columns().splitlines():
    print("[bootstrap]", line)
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
