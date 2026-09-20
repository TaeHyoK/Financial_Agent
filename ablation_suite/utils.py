"""Small, dependency-free filesystem and process helpers."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, IO, Sequence


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_label(value: str) -> str:
    label = str(value).strip() or "item"
    for character in ('\\', '/', ':', '*', '?', '"', '<', '>', '|', ' '):
        label = label.replace(character, "_")
    return label.strip("._") or "item"


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, payload: Any) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return destination


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_seed(base_seed: int, selected_date: str, company_name: str) -> int:
    payload = f"{base_seed}:{selected_date}:{company_name}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def python_env(final_src: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    current = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(final_src) if not current else str(final_src) + os.pathsep + current
    if extra:
        env.update(extra)
    return env


def run_logged(
    command: Sequence[str],
    *,
    log_path: Path,
    cwd: Path,
    env: dict[str, str],
    timeout: int | None = None,
) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n[{utc_now()}] COMMAND {json.dumps(list(command), ensure_ascii=False)}\n")
        log.flush()
        completed = subprocess.run(
            list(command),
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=timeout,
        )
        log.write(f"[{utc_now()}] RETURN_CODE {completed.returncode}\n")
        return completed.returncode


def tail_text(path: Path, character_count: int = 4000) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - character_count * 4))
        return handle.read().decode("utf-8", errors="replace")[-character_count:]


class SuiteLock:
    """Prevent two background processes from writing the same suite."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: IO[str] | None = None

    def __enter__(self) -> "SuiteLock":
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"Another process already owns suite lock: {self.path}") from exc
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(str(os.getpid()))
        self.handle.flush()
        return self

    def __exit__(self, *_args: object) -> None:
        if self.handle is None:
            return
        import fcntl

        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
