from __future__ import annotations

import datetime as dt
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path


def json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral() else float(value)
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def dumps(value, **kwargs):
    return json.dumps(value, ensure_ascii=False, default=json_default, allow_nan=False, **kwargs)


def load_jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except ValueError as error:
                    raise ValueError(f"Invalid JSON at {path.name}:{number}") from error


def write_json(path: Path, value):
    nonfinite=[]
    def safe(item,location='$'):
        if isinstance(item,float) and not math.isfinite(item) or isinstance(item,Decimal) and not item.is_finite():
            nonfinite.append({'path':location,'value':str(item)})
            return None
        if isinstance(item,dict):
            return {key:safe(child,location+'.'+str(key)) for key,child in item.items()}
        if isinstance(item,(list,tuple)):
            return [safe(child,location+'['+str(i)+']') for i,child in enumerate(item)]
        return item
    value=safe(value)
    if nonfinite and isinstance(value,dict):
        value['_nonfinite_values']=nonfinite
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def fingerprint(paths: list[Path], *, version: str = "") -> str:
    digest = hashlib.sha256(version.encode())
    for path in sorted(paths):
        digest.update(path.name.encode())
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()
