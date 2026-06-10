from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOUL_PATH = PROJECT_ROOT / "SOUL.md"


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
