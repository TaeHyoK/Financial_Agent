from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


def _load_sy_agent_module() -> Any:
    module_path = Path(__file__).resolve().parent / "SY_Agent" / "sy_agent.py"
    spec = importlib.util.spec_from_file_location("news_sy_agent_cli_runtime", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load News SY Agent module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    _load_sy_agent_module().main()


if __name__ == "__main__":
    main()
