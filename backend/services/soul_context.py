from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.config.runtime_paths import PROJECT_ROOT, default_soul_path

SOUL_PATH = default_soul_path()


@dataclass(frozen=True)
class SoulContext:
    content: str
    loaded: bool
    path: str


def load_soul_context(path: str | Path = SOUL_PATH) -> SoulContext:
    soul_path = Path(path)
    if not soul_path.exists():
        return SoulContext(content="", loaded=False, path=str(soul_path))
    return SoulContext(
        content=soul_path.read_text(encoding="utf-8").strip(),
        loaded=True,
        path=str(soul_path),
    )
