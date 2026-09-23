"""Make the source and runner packages importable without an editable install."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

for entry in (ROOT / "src", ROOT / "run_config"):
    path = str(entry)
    if path in sys.path:
        sys.path.remove(path)
    sys.path.insert(0, path)
