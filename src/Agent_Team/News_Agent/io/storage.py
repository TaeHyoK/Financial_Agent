"""Storage utilities for parquet/jsonl."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Sequence


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_jsonl(records: Iterable[dict], path: str | Path) -> None:
    path = Path(path)
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")


def read_jsonl(path: str | Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def save_parquet(records: Sequence[dict], path: str | Path) -> None:
    path = Path(path)
    ensure_dir(path.parent)
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("pandas가 설치되어 있어야 parquet 저장이 가능합니다.") from exc
    df = pd.DataFrame.from_records(records)
    df.to_parquet(path, index=False)


def save_json(records: dict, path: str | Path) -> None:
    path = Path(path)
    ensure_dir(path.parent)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
