from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import analytics_core


def _stock_frame() -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-05", periods=81)
    return pd.DataFrame({"Close": np.arange(100.0, 181.0)}, index=dates)


def _history(path, stock: pd.DataFrame) -> None:
    pd.DataFrame(
        [
            {
                "Ticker": "600000.SH",
                "TradeDate": stock.index[0].strftime("%Y-%m-%d"),
                "Close": stock["Close"].iloc[0],
            }
        ]
    ).to_csv(path, index=False, encoding="utf-8-sig")


def test_refresh_outcomes_uses_exact_benchmark_window(tmp_path, monkeypatch) -> None:
    stock = _stock_frame()
    benchmark = pd.DataFrame(
        {"Close": np.arange(200.0, 362.0, 2.0)},
        index=stock.index,
    )
    history_path = tmp_path / "SignalHistory.csv"
    _history(history_path, stock)
    monkeypatch.setattr(analytics_core, "_load_cache", lambda *_args: stock)
    monkeypatch.setattr(
        analytics_core,
        "_load_benchmark_frames",
        lambda _source: {"沪深300": benchmark},
    )

    refreshed = analytics_core.refresh_research_outcomes("test", history_path)

    assert refreshed.loc[0, "Return20D"] == pytest.approx(20.0)
    assert refreshed.loc[0, "BenchmarkReturn20D"] == pytest.approx(20.0)
    assert refreshed.loc[0, "BenchmarkReturn60D"] == pytest.approx(60.0)


def test_refresh_outcomes_does_not_shift_missing_benchmark_date(
    tmp_path,
    monkeypatch,
) -> None:
    stock = _stock_frame()
    benchmark = pd.DataFrame(
        {"Close": np.arange(200.0, 362.0, 2.0)},
        index=stock.index,
    ).drop(index=stock.index[20])
    history_path = tmp_path / "SignalHistory.csv"
    _history(history_path, stock)
    monkeypatch.setattr(analytics_core, "_load_cache", lambda *_args: stock)
    monkeypatch.setattr(
        analytics_core,
        "_load_benchmark_frames",
        lambda _source: {"沪深300": benchmark},
    )

    refreshed = analytics_core.refresh_research_outcomes("test", history_path)

    assert refreshed.loc[0, "Return20D"] == pytest.approx(20.0)
    assert pd.isna(refreshed.loc[0, "BenchmarkReturn20D"])
    assert refreshed.loc[0, "BenchmarkReturn60D"] == pytest.approx(60.0)
