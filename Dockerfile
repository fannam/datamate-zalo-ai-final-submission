# Zalo AI Challenge 2026 - DataMate. Build: see README.md ("Build") - needs network once, for the weights.
FROM vllm/vllm-openai:v0.27.1-cu129
ENTRYPOINT []

ARG MODEL_REPO=Qwen/Qwen3.5-9B
ARG MODEL_REVISION=c202236235762e1c871ad0ccb60c8ee5ba337b9a
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NCCL_P2P_DISABLE=1 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /code

# Inference weights (pinned revision, downloaded once at build time straight into /code/models,
# so the build context and a COPY layer never hold a second 19 GB copy).
RUN HF_HUB_OFFLINE=0 python3 -c "from huggingface_hub import snapshot_download; \
snapshot_download('${MODEL_REPO}', revision='${MODEL_REVISION}', local_dir='/code/models/Qwen3.5-9B')" \
 && rm -rf /code/models/Qwen3.5-9B/.cache

# Solver dependencies live in their own venv so their pins cannot clash with vLLM's.
COPY requirements.txt /code/requirements.txt
RUN python3 -m venv /opt/dm && /opt/dm/bin/pip install -r /code/requirements.txt
# The guide's two-GPU NCCL workaround (upgrade 2.26.2 -> nvidia-nccl-cu12==2.27.7) is NOT applied:
# this base image already ships nvidia-nccl-cu12 2.30.7, so pinning 2.27.7 would be a downgrade.

COPY training_code/ /code/training_code/
COPY src/ /code/src/
COPY predict.sh /code/
COPY README.md /code/README.md
RUN chmod +x /code/predict.sh
CMD ["bash", "/code/predict.sh"]
