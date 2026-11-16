#!/usr/bin/env bash
# There is no training step: the solution uses the off-the-shelf Qwen3.5-9B weights unchanged.
# This script rebuilds the verification artefacts only (data manifest + checksums).
set -euo pipefail
cd "$(dirname "$0")"
python3 src/make_manifest.py
echo "No fine-tuning was performed. See README.md."
