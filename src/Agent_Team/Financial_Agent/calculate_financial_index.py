"""CLI wrapper for financial index calculation."""

from __future__ import annotations

try:
    from .financial_index_calculator import main
except ImportError:  # pragma: no cover - supports direct script execution
    from financial_index_calculator import main


if __name__ == "__main__":
    main()
