"""Small shared helpers used across the corpus builder.

Plain English: the odds-and-ends every stage reuses. Technically: derive a
stable sub-seed from the master seed (keeps runs reproducible), normalize text
for duplicate detection, and read/write JSONL files of feedback items (writes
go to a temp file first, then rename, so a crash can't leave a half-written file).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path

from echo.schemas.envelope import CorpusItem

_WS = re.compile(r"\s+")


def normalize_text(s: str) -> str:
    """Casefold + whitespace-collapse for duplicate detection."""
    return _WS.sub(" ", (s or "").strip().casefold())


def child_seed(base: int, *parts: object) -> int:
    """Derive a stable 32-bit sub-seed from a base seed and arbitrary parts."""
    h = hashlib.sha256(f"{base}|{'|'.join(map(str, parts))}".encode()).hexdigest()
    return int(h[:8], 16)


def write_jsonl(path: Path, items: Iterable[CorpusItem]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it.model_dump(mode="json"), ensure_ascii=False) + "\n")
            n += 1
    tmp.replace(path)  # atomic
    return n


def read_jsonl(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)
