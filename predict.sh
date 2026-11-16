#!/usr/bin/env bash
# Entry point: /data -> /result/submission.jsonl + /result/logs/. Fully offline. No arguments.
set -uo pipefail

MODEL_DIR=/code/models/Qwen3.5-9B
SERVED_NAME=Qwen/Qwen3.5-9B
PORT=8000
DATA=/data
RESULT=/result
CACHE=/tmp/datamate_cache

export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 NCCL_P2P_DISABLE=1 PYTHONPATH=/code/src
export OPENAI_BASE_URL="http://127.0.0.1:${PORT}/v1" OPENAI_MODEL="$SERVED_NAME" OPENAI_API_KEY=local
mkdir -p "$RESULT/logs" "$CACHE"
# A rerun into a reused directory must not trip the solver's "output exists" guard.
rm -f "$RESULT/submission.jsonl"

NGPU=$(nvidia-smi -L 2>/dev/null | wc -l); [ "$NGPU" -ge 1 ] || NGPU=1; [ "$NGPU" -le 2 ] || NGPU=2

vllm serve "$MODEL_DIR" --served-model-name "$SERVED_NAME" --host 127.0.0.1 --port "$PORT" \
  --dtype bfloat16 --tensor-parallel-size "$NGPU" --max-model-len 32768 --gpu-memory-utilization 0.90 \
  --reasoning-parser qwen3 --seed 42 > "$RESULT/vllm_server.log" 2>&1 &
VLLM_PID=$!
trap 'kill $VLLM_PID 2>/dev/null; wait $VLLM_PID 2>/dev/null' EXIT

for _ in $(seq 1 900); do   # up to 30 min to load
  curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null && break
  kill -0 $VLLM_PID 2>/dev/null || { echo "vLLM exited early:"; tail -40 "$RESULT/vllm_server.log"; exit 1; }
  sleep 2
done
curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null || { echo "vLLM never became ready"; exit 1; }

# Fixed settings (also in training_code/configs/pipeline.json). Answers are appended to submission.jsonl as each
# question finishes; one JSON log per question lands in $RESULT/logs/<id>.json.
/opt/dm/bin/python -m datamate run --split "$DATA" --out "$RESULT" --cache "$CACHE" \
  --concurrency 16 --temperature 0 --specialist off --role-inference fallback \
  --repair rewrite --verify check --deadline 300 --max-tokens 3000 --timeout 600
STATUS=$?
/opt/dm/bin/python -m datamate validate --questions "$DATA/questions.jsonl" --submission "$RESULT/submission.jsonl" || true
exit $STATUS
