from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path


@dataclass(frozen=True)
class ModelSettings:
    base_url: str
    model: str
    api_key: str = field(repr=False)
    timeout: float = 90
    max_tokens: int = 3000
    temperature: float = 0.6
    thinking: bool = False
    retries: int = 2
    stream: bool = False
    thinking_temperature: float | None = None

    @classmethod
    def from_env(cls, path: Path = Path(".env"), **overrides):
        values = {}
        if path.exists():
            for line in path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.removeprefix("export ").split("=", 1)
                    values[key.strip()] = value.strip().strip("\"'")
        required = ("OPENAI_BASE_URL", "OPENAI_MODEL", "OPENAI_API_KEY")
        for key in required:
            if not values.get(key):
                values[key] = os.environ.get(key, "")
            if not values[key]:
                raise ValueError(f"Missing {key}; configure {path}")
        return cls(base_url=values[required[0]].rstrip("/"), model=values[required[1]], api_key=values[required[2]], **overrides)

    def public_dict(self):
        return {key: value for key, value in self.__dict__.items() if key != "api_key"}
