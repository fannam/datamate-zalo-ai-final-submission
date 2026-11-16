"""Write data_manifest.json: relative path, size, SHA-256 of every file under data/, configs/."""
import hashlib, json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
files = []
for sub in ("data", "configs"):
    for p in sorted((root / sub).rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            files.append({"path": f"{sub}/{p.relative_to(root / sub)}", "bytes": p.stat().st_size,
                          "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
count = lambda f: sum(1 for _ in (root / f).open()) if (root / f).exists() else None
manifest = {
    "training_performed": False,
    "base_model": {"name": "Qwen/Qwen3.5-9B", "url": "https://huggingface.co/Qwen/Qwen3.5-9B",
                   "revision": "c202236235762e1c871ad0ccb60c8ee5ba337b9a", "license": "apache-2.0",
                   "released": "2026-02-27", "parameters": 9653104368,
                   "inference_weights_in_image": "/code/models/Qwen3.5-9B"},
    "datasets": {"train_questions": count("data/train/questions.jsonl"), "train_answers": count("data/train/answers.jsonl"),
                 "note": "official provided train split; used for rule design/evaluation only, never for weight updates"},
    "files": files,
}
(root / "data_manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
print(len(files), "files hashed")
