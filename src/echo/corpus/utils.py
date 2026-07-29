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
    """Write a list of CorpusItem records to a JSONL file and return how many were written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it.model_dump(mode="json"), ensure_ascii=False) + "\n")
            n += 1
    # Write to a temp file first, then rename, so a crash mid-write never leaves a half-written file in place.
    tmp.replace(path)  # atomic
    return n


def read_jsonl(path: Path) -> Iterator[dict]:
    """Read a JSONL file line by line and yield each line as a parsed dict."""
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)
