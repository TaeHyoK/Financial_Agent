"""Step status models for orchestration runs."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


PENDING = "pending"
RUNNING = "running"
SUCCESS = "success"
FAILED = "failed"
SKIPPED = "skipped"
PARTIAL_SUCCESS = "partial_success"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def truncate_text(value: str, limit: int = 12000) -> str:
    if len(value) <= limit:
        return value
    return value[-limit:]


@dataclass
class StepRecord:
    name: str
    status: str = PENDING
    command: list[str] = field(default_factory=list)
    started_at: str | None = None
    ended_at: str | None = None
    duration_seconds: float | None = None
    returncode: int | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    reason: str | None = None
    outputs: dict[str, str] = field(default_factory=dict)
    input_fingerprint: str = ""

    def start(self, command: list[str]) -> float:
        self.status = RUNNING
        self.command = command
        self.started_at = utc_now()
        return time.monotonic()

    def finish(self, *, status: str, started: float, returncode: int | None = None, stdout: str = "", stderr: str = "", reason: str | None = None) -> None:
        self.status = status
        self.ended_at = utc_now()
        self.duration_seconds = round(time.monotonic() - started, 3)
        self.returncode = returncode
        self.stdout_tail = truncate_text(stdout)
        self.stderr_tail = truncate_text(stderr)
        self.reason = reason

    def skip(self, reason: str) -> None:
        self.status = SKIPPED
        self.started_at = utc_now()
        self.ended_at = self.started_at
        self.duration_seconds = 0.0
        self.reason = reason

    def reuse(self, reason: str) -> None:
        self.status = SUCCESS
        self.started_at = utc_now()
        self.ended_at = self.started_at
        self.duration_seconds = 0.0
        self.returncode = 0
        self.reason = reason

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "command": self.command,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": self.duration_seconds,
            "returncode": self.returncode,
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
            "reason": self.reason,
            "outputs": self.outputs,
            "input_fingerprint": self.input_fingerprint,
        }
