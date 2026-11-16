# training_code

**No training, fine-tuning or adapter was used.** Inference runs the original Qwen3.5-9B checkpoint
(https://huggingface.co/Qwen/Qwen3.5-9B, revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a`, Apache-2.0),
stored in the image at `/code/models/Qwen3.5-9B`. No base checkpoint is omitted from the image.

This folder holds the data used to develop and evaluate the rules, the code that generated the
self-generated data, and a checksum manifest.

## Contents

| Path | Content |
|---|---|
| `data/train/` | Official train split: `questions.jsonl` and `answers.jsonl` (3,000 each), `rulebook.md`, `calendar.csv`, shop files of 30 businesses under `data/` |
| `data/manifests/groups.json` | Shop-level split, seed 42: build 18 shops, dev 6, lockbox 6 |
| `data/manifests/{build,dev,lockbox}.jsonl` | Questions of each split: 1,736 / 632 / 632 |
| `data/manifests/*.jsonl` (others) | Fixed question samples of the train split used for quick checks |
| `data/manifests/repair_eval/` | Train and paraphrase questions used to evaluate the rewrite step |
| `data/synthetic/paraphrases_v1/` | Paraphrase set 1: 750 generated, 663 kept |
| `data/synthetic/paraphrases_v2/` | Paraphrase set 2: 450 generated, 404 kept |
| `data_generation/make_paraphrases.py` | Generates a paraphrase set from train questions |
| `data_generation/synth_phone.py`, `synth_baby.py`, `synth_repair.py` | Build small phone, mother-and-baby and repair shops with known answers |
| `data_generation/replay_frames.py` | Re-compiles stored model frames without model calls |
| `configs/pipeline.json` | Fixed inference settings used by `/code/predict.sh` |
| `src/make_manifest.py`, `train.sh` | Regenerate `data_manifest.json` |
| `data_manifest.json` | Relative path, size and SHA-256 of every file in `data/`, `data_generation/`, `configs/` |
| `logs/`, `checkpoints/` | Empty: nothing was trained |
| `requirements.txt` | Pinned dependencies (same as `/code/requirements.txt`) |

## Paraphrase sets

Each set rewrites train questions in five styles (spoken, no diacritics, Telex typing, reordered, extra
context). The model sees only the question text and a style instruction, never answers. Each variant keeps
the original shop and `as_of_date`, so the train answer still applies; variants that change any number or date
token are dropped.

| Set | Generator | Temperature | Seed | Per shop | Generated | Kept |
|---|---|---:|---:|---:|---:|---:|
| `paraphrases_v1` | Qwen3.5-9B | 0.7 | 42 | 25 | 750 | 663 |
| `paraphrases_v2` | Qwen3.5-9B | 0.7 | 7 | 15 | 450 | 404 |

Settings are recorded in each set's `meta.json`. Generation is sampled and therefore **not bit-reproducible**;
every prompt and raw model response is stored in `generation.jsonl` so the sets can be verified as produced.

Regenerating a set (needs an OpenAI-compatible endpoint serving Qwen3.5-9B, configured in `.env`):

```bash
PYTHONPATH=src python training_code/data_generation/make_paraphrases.py --per-shop 25 --seed 42 \
  --out training_code/data/synthetic/paraphrases_v1
```

The scripts import the solver package `datamate`, so run them with `PYTHONPATH` pointing at the solver source
(`src/` in the repository, `/code/src` in the image). The generated shops follow the released rulebook and
expect the challenge data at the repository root (`train/calendar.csv`, `public_test/rulebook.md`). They are
deterministic and are rebuilt by the unit tests and by:

```bash
PYTHONPATH=src python training_code/data_generation/synth_phone.py --out runs/synth_phone
```

## Verifying the manifest

```bash
bash /code/training_code/train.sh     # rewrites data_manifest.json; CPU only, a few seconds
```

Hardware: any CPU. Runtime: seconds. No GPU and no network are needed.
