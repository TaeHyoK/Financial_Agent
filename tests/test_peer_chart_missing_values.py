"""Missing peer financial values must not crash or become zero estimates."""
from pathlib import Path
import sys
from unittest.mock import patch

import matplotlib
matplotlib.use("Agg")
import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from Agent_Team.Visualization_Agent.chart_builders import build_peer_profitability_comparison_chart


@pytest.mark.parametrize("eps", [[None, None], [100.0, None], [-100.0, 0.0]])
def test_missing_eps_preserved(tmp_path, eps):
    frame = pd.DataFrame({"company_name": ["대상기업", "비교기업"],
                          "revenue_100m": [100.0, None], "contribution_margin_pct": [None, 10.0],
                          "sga_margin_pct": [5.0, None], "eps": eps})
    saved = []
    with patch("Agent_Team.Visualization_Agent.chart_builders._save_figure", side_effect=lambda fig, *_: saved.append(fig)):
        build_peer_profitability_comparison_chart(frame, tmp_path / "chart.pdf", tmp_path / "chart.png")
    heights = [bar.get_height() for bar in saved[0].axes[2].patches]
    for expected, actual in zip(eps, heights):
        assert np.isnan(actual) if expected is None else actual == expected
    assert frame["eps"].isna().sum() == sum(value is None for value in eps)
    assert sum(t.get_text() == "자료 없음" for t in saved[0].axes[2].texts) == sum(value is None for value in eps)


def test_all_missing_rejected(tmp_path):
    frame = pd.DataFrame({"company_name": ["기업"], **{key: [None] for key in (
        "revenue_100m", "contribution_margin_pct", "sga_margin_pct", "eps")}})
    with pytest.raises(ValueError, match="no usable numeric data"):
        build_peer_profitability_comparison_chart(frame, tmp_path / "chart.pdf", tmp_path / "chart.png")
