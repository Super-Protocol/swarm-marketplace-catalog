#!/usr/bin/env bash
#
# Confidential RAG, everything that does not need the card, on Docker.
#
#   charts/tests/smoke/confidential-rag.sh
#   KEEP=1 charts/tests/smoke/confidential-rag.sh      # leave it up to poke at
#
# Why this exists. Every expensive defect in this listing's history was found by
# deploying it to the epic's single H100 and reading what came back: `topK` above
# the reranker's batch ceiling (0.1.6), thread pools sized from the host rather
# than the cgroup (0.1.7), and before those a probe that killed the embedder
# mid-index and a Valkey container that could not start. Each cost a gated
# deployment to find and another to confirm, and digest stabilisation was the
# most expensive line in the whole pilot — 2 h 15 of 5 h 20.
#
# Almost none of that needed a GPU. Retrieval, reranking, extraction, indexing,
# the console and the status reporting are all CPU, and they are where the bugs
# were. So this stands them up locally, with the SAME images the listing pins
# and the same configuration the charts render, and asks the deployment for the
# two things 0.1.8 adds:
#
#   1. a reranker that refuses the batch is reported as degraded, and the
#      citations it degraded carry the retriever's own scores rather than 0.0;
#   2. a submitted document is extracted, indexed and retrievable, with a table
#      searchable as a grid and a chart located rather than transcribed.
#
# What it does NOT cover: generation. vLLM needs the card, and nothing here
# changes it — `/v1/search` is the endpoint that retrieves, reranks and cites
# without a model writing prose, which is exactly the half this exercises.
#
# The weights come from Hugging Face on first run (about 3.5 GiB, cached in a
# Docker volume between runs). The extraction image carries its own models and
# fetches nothing.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../../.." && pwd)"
cd "$root"

NET=crag-smoke
KEY=smoke-inference-key-not-for-production
INGEST_KEY=smoke-ingest-key-not-for-production
CONSOLE_PASSWORD=smoke-console-password-not-for-production
SESSION_KEY=smoke-session-signing-key-not-for-production
VECTOR_PASSWORD=smoke-vector-store-password-not-prod
COLLECTION=documents
UPLOADS=documents_uploads

# The digests the listing pins. Read from the charts rather than written here, so
# this runs what a deployment would run and fails loudly when it stops matching.
pin() { python3 - "$1" "$2" <<'PY'
import sys, yaml
values = yaml.safe_load(open(sys.argv[1]))
node = values
for key in sys.argv[2].split("."):
    node = node[key]
print(f"{node['registry']}/{node['repository']}@{node['digest']}")
PY
}
TEI_IMAGE="$(pin charts/text-embeddings-server/values.yaml image)"
MILVUS_IMAGE="$(pin charts/milvus-standalone/values.yaml image)"
INGEST_IMAGE="$(pin charts/swarm-doc-ingest/values.yaml image)"
RAG_IMAGE="$(pin charts/confidential-rag/values.yaml image)"

failures=0
note() { printf '\n\033[1m%s\033[0m\n' "$*"; }
pass() { printf '  ok    %s\n' "$*"; }
fail() { printf '  FAIL  %s\n' "$*"; failures=$((failures + 1)); }

cleanup() {
  if [ -n "${KEEP:-}" ]; then
    printf '\nKEEP=1: left up. Console http://localhost:18080, rag-server http://localhost:18081\n'
    printf 'Tear down with: docker rm -f crag-{milvus,embedder,reranker,ingest,server,corpus,console}; docker network rm %s\n' "$NET"
    return
  fi
  docker rm -f crag-milvus crag-embedder crag-reranker crag-ingest crag-server \
      crag-corpus crag-console >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker rm -f crag-milvus crag-embedder crag-reranker crag-ingest crag-server \
    crag-corpus crag-console >/dev/null 2>&1 || true
docker network rm "$NET" >/dev/null 2>&1 || true
docker network create "$NET" >/dev/null
docker volume create crag-weights >/dev/null
docker volume create crag-milvus-data >/dev/null

wait_for() {
  local label="$1" tries="$2"; shift 2
  for _ in $(seq "$tries"); do
    if "$@" >/dev/null 2>&1; then pass "$label"; return 0; fi
    sleep 5
  done
  fail "$label did not come up"
  docker logs --tail 40 "crag-${label%% *}" 2>&1 | sed 's/^/        /' || true
  return 1
}

# Probed from the HOST, over a published port, rather than with `docker exec`.
# Three of these images carry curl, two carry a Python interpreter and none
# carries both — a property of slim images rather than a fault, and not a thing
# worth discovering one container at a time. Every service here therefore
# publishes its port on localhost, and one probe serves all of them.
reachable() {
  python3 "$here/reachable.py" "$1"
}

# ---------------------------------------------------------------------------
# The stack, with the charts' own configuration
# ---------------------------------------------------------------------------

note "Milvus (embedded etcd, local storage, authorisation on — the chart's shape)"
work="$(mktemp -d)"
cat > "$work/embedEtcd.yaml" <<'EOF'
listen-client-urls: http://127.0.0.1:2379
advertise-client-urls: http://127.0.0.1:2379
quota-backend-bytes: 4294967296
auto-compaction-mode: revision
auto-compaction-retention: '1000'
EOF
cat > "$work/user.yaml" <<EOF
common:
  security:
    authorizationEnabled: true
    defaultRootPassword: $VECTOR_PASSWORD
EOF
docker run -d --name crag-milvus --network "$NET" -p 19091:9091 \
  -e ETCD_USE_EMBED=true -e ETCD_DATA_DIR=/var/lib/milvus/etcd \
  -e ETCD_CONFIG_PATH=/milvus/configs/embedEtcd.yaml \
  -e COMMON_STORAGETYPE=local -e DEPLOY_MODE=STANDALONE \
  -v crag-milvus-data:/var/lib/milvus \
  -v "$work/embedEtcd.yaml:/milvus/configs/embedEtcd.yaml:ro" \
  -v "$work/user.yaml:/milvus/configs/user.yaml:ro" \
  "$MILVUS_IMAGE" milvus run standalone >/dev/null
wait_for "milvus" 60 reachable http://localhost:19091/healthz

note "the embedder and the reranker, with the presets' own batch ceilings"
# `--max-client-batch-size 32` on the reranker is the number that made every
# rerank of 0.1.5 fail with a 413, and the number this run reproduces on purpose.
docker run -d --name crag-embedder --network "$NET" -p 18091:80 \
  -e HF_HUB_ENABLE_HF_TRANSFER=0 -v crag-weights:/data \
  "$TEI_IMAGE" --model-id BAAI/bge-large-en-v1.5 --pooling cls \
  --max-batch-tokens 2048 --max-client-batch-size 32 --auto-truncate \
  --api-key "$KEY" --hostname 0.0.0.0 --port 80 --json-output >/dev/null
docker run -d --name crag-reranker --network "$NET" -p 18092:80 \
  -e HF_HUB_ENABLE_HF_TRANSFER=0 -v crag-weights:/data \
  "$TEI_IMAGE" --model-id BAAI/bge-reranker-v2-m3 \
  --max-batch-tokens 2048 --max-client-batch-size 32 --auto-truncate \
  --api-key "$KEY" --hostname 0.0.0.0 --port 80 --json-output >/dev/null
wait_for "embedder" 180 reachable http://localhost:18091/health
wait_for "reranker" 180 reachable http://localhost:18092/health

note "document extraction (models baked, hub offline)"
docker run -d --name crag-ingest --network "$NET" -p 18093:8080 \
  -e INGEST_API_KEY="$INGEST_KEY" "$INGEST_IMAGE" >/dev/null
wait_for "ingest" 60 reachable http://localhost:18093/v1/health

# Everything the chart's ConfigMap carries, read out of the render rather than
# out of `files/`: what runs in a deployment is the rendered ConfigMap.
note "the pipeline, its indexer and its console, from the rendered ConfigMap"
app="$(mktemp -d)"
helm template rag charts/confidential-rag --namespace confidential-rag --skip-tests \
  --values charts/tests/cases/rag-stack.yaml \
  | python3 "$here/unpack-app.py" "$app" >/dev/null

pipeline_env=(
  -e APP_VECTORSTORE_NAME=milvus
  -e APP_VECTORSTORE_URL=http://crag-milvus:19530
  -e APP_VECTORSTORE_USERNAME=root
  -e APP_VECTORSTORE_PASSWORD="$VECTOR_PASSWORD"
  -e APP_VECTORSTORE_INDEXTYPE=HNSW
  -e APP_VECTORSTORE_SEARCHTYPE=dense
  -e APP_VECTORSTORE_ENABLEGPUINDEX=False
  -e APP_VECTORSTORE_ENABLEGPUSEARCH=False
  -e APP_VECTORSTORE_EF=100
  -e COLLECTION_NAME="$COLLECTION"
  -e VECTOR_DB_TOPK=12
  -e APP_RETRIEVER_TOPK=6
  -e APP_EMBEDDINGS_MODELENGINE=openai
  -e APP_EMBEDDINGS_MODELNAME=bge-large-en-v1.5
  -e APP_EMBEDDINGS_SERVERURL=http://crag-embedder/v1
  -e APP_EMBEDDINGS_DIMENSIONS=1024
  -e APP_EMBEDDINGS_APIKEY="$KEY"
  -e ENABLE_RERANKER=True
  -e APP_RANKING_MODELENGINE=tei
  -e APP_RANKING_MODELNAME=bge-reranker-v2-m3
  -e APP_RANKING_SERVERURL=http://crag-reranker
  -e APP_RANKING_APIKEY="$KEY"
  -e RERANKER_SCORE_THRESHOLD=0.0
  -e RERANKER_STATUS_PATH=/tmp-data/reranker-status.json
  # No card here, so generation is pointed at nothing on purpose: the three LLM
  # clients are constructed at start-up and not called by `/v1/search`.
  -e APP_LLM_MODELENGINE=openai
  -e APP_LLM_MODELNAME=llama-3.2-3b-instruct
  -e APP_LLM_SERVERURL=http://127.0.0.1:9/v1
  -e APP_LLM_APIKEY="$KEY"
  -e APP_QUERYREWRITER_MODELNAME=llama-3.2-3b-instruct
  -e APP_QUERYREWRITER_SERVERURL=http://127.0.0.1:9/v1
  -e APP_FILTEREXPRESSIONGENERATOR_MODELNAME=llama-3.2-3b-instruct
  -e APP_FILTEREXPRESSIONGENERATOR_SERVERURL=http://127.0.0.1:9/v1
  -e SUMMARY_LLM=llama-3.2-3b-instruct
  -e SUMMARY_LLM_SERVERURL=http://127.0.0.1:9/v1
  -e ENABLE_QUERYREWRITER=False -e ENABLE_FILTER_GENERATOR=False
  -e ENABLE_REFLECTION=False -e ENABLE_QUERY_DECOMPOSITION=False
  -e ENABLE_AGENTIC_RAG=False -e ENABLE_VLM_INFERENCE=False
  -e ENABLE_GUARDRAILS=False -e ENABLE_CITATIONS=True
  -e OBJECTSTORE_BACKEND=filesystem -e OBJECTSTORE_LOCAL_PATH=/data/objects
  -e REDIS_HOST=127.0.0.1 -e REDIS_PORT=6379 -e REDIS_DB=0
  -e LOGLEVEL=INFO -e LOG_LEVEL=INFO
  -e TEMP_DIR=/tmp-data
)

docker run -d --name crag-server --network "$NET" -p 18081:8081 \
  "${pipeline_env[@]}" "$RAG_IMAGE" \
  --host 0.0.0.0 --port 8081 --workers 2 >/dev/null
wait_for "server" 60 reachable http://localhost:18081/v1/health

# `--entrypoint`, because the image's own is uvicorn and these two are the
# chart's `command:` overriding it — the indexer is a loop and the console is a
# different application in the same image.
docker run -d --name crag-corpus --network "$NET" -p 18085:8085 \
  --entrypoint python3 \
  "${pipeline_env[@]}" \
  -e UPLOAD_COLLECTION_NAME="$UPLOADS" \
  -e INGEST_URL=http://crag-ingest:8080 \
  -e INGEST_API_KEY="$INGEST_KEY" \
  -e CORPUS_BINDING_FILE=/etc/corpus/corpus-binding.json \
  -e CORPUS_POLL_SECONDS=30 -e CORPUS_STATUS_PORT=8085 \
  -e CORPUS_CHUNK_CHARS=1800 -e CORPUS_EMBED_BATCH=4 \
  -v "$app:/opt/app:ro" \
  "$RAG_IMAGE" /opt/app/corpus_sync.py >/dev/null
wait_for "corpus" 60 reachable http://localhost:18085/healthz

docker run -d --name crag-console --network "$NET" -p 18080:8080 \
  --entrypoint uvicorn \
  -e PYTHONPATH=/opt/console \
  -e CONSOLE_TITLE="Confidential RAG" \
  -e CONSOLE_PASSWORD="$CONSOLE_PASSWORD" \
  -e CONSOLE_SESSION_KEY="$SESSION_KEY" \
  -e RAG_SERVER_URL=http://crag-server:8081 \
  -e CORPUS_SYNC_URL=http://crag-corpus:8085 \
  -e COLLECTION_NAME="$COLLECTION" \
  -e UPLOAD_COLLECTION_NAME="$UPLOADS" \
  -e UPLOAD_MAX_BYTES=67108864 \
  -e ABOUT_GENERATION_MODEL=llama-3.2-3b-instruct \
  -e ABOUT_EMBEDDING_MODEL=bge-large-en-v1.5 \
  -e ABOUT_RERANKER_MODEL=bge-reranker-v2-m3 \
  -e ABOUT_EXTRACTION_STACK="Docling + TableFormer + RapidOCR" \
  -e UI_DIR=/opt/console \
  -v "$app:/opt/console:ro" \
  "$RAG_IMAGE" console:app --host 0.0.0.0 --port 8080 --workers 1 >/dev/null
wait_for "console" 60 reachable http://localhost:18080/healthz

# ---------------------------------------------------------------------------
# What it is for
# ---------------------------------------------------------------------------
note "the questions this run exists to ask"
if python3 "$here/confidential-rag-checks.py" \
      --console http://localhost:18080 \
      --password "$CONSOLE_PASSWORD" \
      --rag http://localhost:18081 \
      --collection "$COLLECTION" --uploads "$UPLOADS"; then
  :
else
  failures=$((failures + 1))
  printf '\n  the rag-server said:\n'
  docker logs --tail 60 crag-server 2>&1 | sed 's/^/        /'
  printf '\n  the indexer said:\n'
  docker logs --tail 40 crag-corpus 2>&1 | sed 's/^/        /'
fi

printf '\n\033[1mResult\033[0m\n'
if [ "$failures" -eq 0 ]; then
  printf '  everything passed\n'
else
  printf '  %d check(s) failed\n' "$failures"
  exit 1
fi
