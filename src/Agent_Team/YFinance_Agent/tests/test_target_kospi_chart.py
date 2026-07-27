from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from Agent_Team.YFinance_Agent.target_kospi_chart import (
    KOSPI_COLOR,
    TARGET_COLOR,
    build_indexed_frame,
    build_target_vs_kospi_chart,
)


def test_build_indexed_frame_uses_common_dates_and_rebases_to_100() -> None:
    target = pd.Series(
        [100.0, 110.0, 121.0],
        index=pd.to_datetime(["2023-10-31", "2023-11-01", "2023-11-02"]),
    )
    kospi = pd.Series(
        [200.0, 210.0, 220.0],
        index=pd.to_datetime(["2023-10-31", "2023-11-01", "2023-11-03"]),
    )

    frame = build_indexed_frame(
        target_prices=target,
        kospi_prices=kospi,
        start_date=date(2023, 10, 31),
        end_exclusive=date(2023, 11, 4),
    )

    assert frame["date"].dt.strftime("%Y-%m-%d").tolist() == ["2023-10-31", "2023-11-01"]
    assert frame["target_index"].tolist() == pytest.approx([100.0, 110.0])
    assert frame["kospi_index"].tolist() == pytest.approx([100.0, 105.0])


def test_chart_builder_writes_png_data_and_point_in_time_metadata(tmp_path) -> None:
    dates = pd.date_range("2023-10-31", periods=6, freq="D")
    target = pd.Series([100, 101, 102, 103, 104, 105], index=dates, dtype=float)
    kospi = pd.Series([100, 100, 101, 101, 102, 102], index=dates, dtype=float)

    result = build_target_vs_kospi_chart(
        ticker="326030.KS",
        company_name="SK바이오팜",
        selected_date=date(2025, 10, 31),
        output_dir=tmp_path,
        output_name="custom.png",
        target_prices=target,
        kospi_prices=kospi,
    )

    assert result.chart_path.name == "custom.png"
    assert result.chart_path.exists()
    assert result.data_path.exists()
    metadata = result.metadata_path.read_text(encoding="utf-8")
    assert '"integrated_into_final_report": false' in metadata
    assert '"selected_date_policy": "before_market_open"' in metadata
    assert TARGET_COLOR in metadata
    assert KOSPI_COLOR in metadata
