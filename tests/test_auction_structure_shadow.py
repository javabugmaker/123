from __future__ import annotations

import json

import numpy as np
import pandas as pd

import institution_scanner.auction_structure as model
import institution_scanner.auction_structure_cli as cli
from institution_scanner.auction_structure import (
    MODEL_PRODUCTION_APPLIED,
    MODEL_ROLE,
    SCORE_WEIGHTS,
    AuctionStructureConfig,
    backtest_auction_structure,
    compute_auction_structure,
    latest_auction_structure,
    summarize_auction_backtest,
)


def _market_frames(size: int = 520) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(20260829)
    index = pd.bdate_range("2023-01-02", periods=size)
    returns = rng.normal(0.0006, 0.014, size)
    close = 20.0 * np.exp(np.cumsum(returns))
    open_ = np.r_[close[0], close[:-1]] * (1.0 + rng.normal(0.0, 0.003, size))
    high = np.maximum(open_, close) * (1.0 + rng.uniform(0.002, 0.015, size))
    low = np.minimum(open_, close) * (1.0 - rng.uniform(0.002, 0.015, size))
    volume = rng.integers(4_000_000, 12_000_000, size).astype(float)
    stock = pd.DataFrame(
        {
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
        },
        index=index,
    )

    benchmark_returns = rng.normal(0.0003, 0.008, size)
    benchmark_close = 4_000.0 * np.exp(np.cumsum(benchmark_returns))
    benchmark = pd.DataFrame(
        {
            "Open": benchmark_close,
            "High": benchmark_close * 1.008,
            "Low": benchmark_close * 0.992,
            "Close": benchmark_close,
            "Volume": volume * 20.0,
        },
        index=index,
    )
    return stock, benchmark


def test_score_contract_is_bounded_and_shadow_only() -> None:
    stock, benchmark = _market_frames()
    before = stock.copy(deep=True)
    result = compute_auction_structure(
        stock,
        benchmark,
        ticker="002961.SZ",
        config=AuctionStructureConfig(minimum_turnover=0.0),
    )

    assert sum(SCORE_WEIGHTS.values()) == 100.0
    assert result["AuctionStructureScore"].dropna().between(0.0, 100.0).all()
    assert result["AuctionStructureCoverage"].dropna().between(0.0, 1.0).all()
    assert result["AuctionStructureModelRole"].eq(MODEL_ROLE).all()
    assert result["AuctionStructureProductionApplied"].eq(False).all()
    assert MODEL_PRODUCTION_APPLIED is False
    pd.testing.assert_frame_equal(stock, before)


def test_auction_value_area_is_ordered_and_chinese_snapshot_is_clear() -> None:
    stock, benchmark = _market_frames()
    config = AuctionStructureConfig(minimum_turnover=0.0)
    result = compute_auction_structure(
        stock,
        benchmark,
        ticker="601899.SH",
        config=config,
    )
    ready = result.loc[result["ProfileReady"]]
    assert not ready.empty
    assert ready["VAL"].le(ready["POC"]).all()
    assert ready["POC"].le(ready["VAH"]).all()

    snapshot = latest_auction_structure(
        stock,
        benchmark,
        ticker="601899.SH",
        config=config,
    )
    assert snapshot.model_role == "SHADOW_CHALLENGER"
    assert snapshot.production_applied is False
    assert snapshot.market in {"风险偏好", "震荡混合", "风险规避"}
    assert "周线" in snapshot.trend and "日线" in snapshot.trend
    assert snapshot.volume in {"需求主导", "供给主导", "承接吸收", "成交缩量", "量价中性"}


def test_historical_prefix_matches_full_run_at_same_confirmed_bar() -> None:
    stock, benchmark = _market_frames(560)
    config = AuctionStructureConfig(minimum_turnover=0.0)
    cutoff = 437
    full = compute_auction_structure(
        stock,
        benchmark,
        ticker="002961.SZ",
        config=config,
    )
    prefix = compute_auction_structure(
        stock.iloc[: cutoff + 1],
        benchmark.iloc[: cutoff + 1],
        ticker="002961.SZ",
        config=config,
    )
    columns = [
        "POC",
        "VAH",
        "VAL",
        "AVWAP",
        "RS20",
        "RS60",
        "ValueMigrationATR",
        "AuctionStructureScore",
        "CandidateSetupCode",
        "LifecycleStateCode",
        "LastPlanCode",
    ]
    for column in columns:
        left = full[column].iloc[cutoff]
        right = prefix[column].iloc[-1]
        if isinstance(left, str):
            assert left == right
        else:
            assert np.isclose(float(left), float(right), equal_nan=True)


def test_missing_benchmark_fails_closed_without_inventing_market_or_rs() -> None:
    stock, _benchmark = _market_frames()
    result = compute_auction_structure(
        stock,
        None,
        ticker="002961.SZ",
        config=AuctionStructureConfig(minimum_turnover=0.0),
    )
    latest = result.iloc[-1]
    assert latest["MarketState"] == "基准不足"
    assert latest["RelativeStrengthState"] == "数据不足"
    assert bool(latest["HardBlock"]) is True
    assert latest["HardBlockReason"] == "核心数据不足"
    assert float(latest["AuctionStructureCoverage"]) < 1.0
    assert latest["ActiveSetupCode"] == "NONE"


def test_lifecycle_requires_distinct_confirmed_bars_for_each_transition() -> None:
    index = pd.bdate_range("2026-01-05", periods=5)
    features = pd.DataFrame(
        {
            "Close": [10.0, 10.1, 10.0, 10.1, 12.1],
            "Open": [10.0] * 5,
            "High": [10.2, 10.2, 10.1, 10.2, 12.2],
            "Low": [9.9, 9.9, 9.9, 10.0, 10.0],
            "ATR": [1.0] * 5,
            "CandidateSetupCode": ["REVERSAL"] * 5,
            "CandidateSourceBar": [7] * 5,
            "CandidatePlanValid": [True] * 5,
            "CandidateEntryLow": [9.8] * 5,
            "CandidateEntryHigh": [10.2] * 5,
            "CandidateInvalidation": [9.0] * 5,
            "CandidateTarget": [12.0] * 5,
            "CandidateRewardRisk": [2.0] * 5,
            "VolumeBehaviorCode": ["DEMAND"] * 5,
            "HigherTimeframeLong": [True] * 5,
            "HigherTimeframePermissive": [True] * 5,
            "CloseLocation": [0.7] * 5,
            "StructureStateCode": ["MSS_UP"] * 5,
            "MarketRegimeCode": ["RISK_ON"] * 5,
            "HardBlock": [False] * 5,
            "ChaseRiskCode": ["LOW"] * 5,
        },
        index=index,
    )
    lifecycle = model._apply_lifecycle(
        features,
        AuctionStructureConfig(minimum_turnover=0.0),
    )
    assert lifecycle["LifecycleEventCode"].tolist() == [
        "SETUP",
        "PLAN_LOCKED",
        "ZONE_TESTED",
        "CONFIRMED",
        "TARGET",
    ]
    assert lifecycle["LastPlanCode"].iloc[-1] == "TARGET"


def test_backtest_enters_next_open_and_never_exits_on_entry_day(monkeypatch) -> None:
    index = pd.bdate_range("2026-03-02", periods=8)
    close = [9.8, 9.9, 10.0, 10.1, 10.2, 12.2, 12.3, 12.4]
    features = pd.DataFrame(
        {
            "Open": [9.8, 9.9, 10.0, 10.1, 10.0, 12.0, 12.3, 12.4],
            "High": np.asarray(close) + 0.2,
            "Low": np.asarray(close) - 0.2,
            "Close": close,
            "Volume": [5_000_000.0] * 8,
            "LifecycleEventCode": ["NONE", "NONE", "NONE", "CONFIRMED", "NONE", "NONE", "NONE", "NONE"],
            "ActiveEntryHigh": [np.nan, np.nan, np.nan, 10.2, np.nan, np.nan, np.nan, np.nan],
            "ATR": [1.0] * 8,
            "ActiveInvalidation": [np.nan, np.nan, np.nan, 9.0, np.nan, np.nan, np.nan, np.nan],
            "ActiveTarget": [np.nan, np.nan, np.nan, 12.0, np.nan, np.nan, np.nan, np.nan],
            "ActiveSetupCode": ["NONE", "NONE", "NONE", "REVERSAL", "NONE", "NONE", "NONE", "NONE"],
            "AuctionStructureScore": [60.0] * 8,
            "AuctionStructureCoverage": [1.0] * 8,
            "ActiveRewardRisk": [np.nan, np.nan, np.nan, 2.0, np.nan, np.nan, np.nan, np.nan],
        },
        index=index,
    )
    monkeypatch.setattr(model, "compute_auction_structure", lambda *args, **kwargs: features)
    source = features[["Open", "High", "Low", "Close", "Volume"]]
    result = backtest_auction_structure(
        source,
        None,
        ticker="600000.SH",
        config=AuctionStructureConfig(minimum_turnover=0.0),
        commission=0.0003,
        slippage=0.0,
    )

    assert len(result.samples) == 1
    sample = result.samples.iloc[0]
    assert sample["SignalDate"] == index[3].strftime("%Y-%m-%d")
    assert sample["EntryDate"] == index[4].strftime("%Y-%m-%d")
    assert sample["ExitDate"] == index[5].strftime("%Y-%m-%d")
    assert int(sample["HoldingBars"]) == 1
    assert sample["Outcome"] == "TARGET"
    assert np.isclose(float(sample["TradingCostPct"]), 0.11)
    assert np.isclose(
        float(sample["NetReturnPct"]),
        float(sample["GrossReturnPct"]) - 0.11,
    )


def test_purged_samples_are_excluded_from_summary_metrics() -> None:
    samples = pd.DataFrame(
        {
            "Split": ["train", "purged", "test"],
            "NetReturnPct": [1.0, 100.0, -1.0],
            "MAEPct": [-1.0, -50.0, -2.0],
            "MFEPct": [2.0, 100.0, 1.0],
            "Outcome": ["TARGET", "TARGET", "INVALIDATED"],
        }
    )
    summary = summarize_auction_backtest(samples)
    assert summary["samples"] == 3
    assert summary["metric_samples"] == 2
    assert summary["purged_samples"] == 1
    assert summary["average_net_return"] == 0.0
    assert summary["win_rate"] == 0.5


def test_cli_writes_separate_shadow_artifacts_and_preserves_production_file(
    monkeypatch,
    tmp_path,
) -> None:
    stock, benchmark = _market_frames()

    def fake_download(ticker: str, **_kwargs):
        return benchmark if ticker == "000300.SH" else stock

    monkeypatch.setattr(cli, "download_ticker", fake_download)
    production = tmp_path / "AllResults.csv"
    production.write_text("Ticker,FinalScore\nKEEP,88\n", encoding="utf-8")
    before = production.read_bytes()
    args = cli.build_parser().parse_args(
        [
            "--tickers",
            "002961.SZ",
            "--output-dir",
            str(tmp_path),
            "--minimum-turnover",
            "0",
            "--max-bars",
            "520",
        ]
    )

    assert cli.run(args) == 0
    assert production.read_bytes() == before
    scores = pd.read_csv(tmp_path / "AuctionStructureShadow.csv", encoding="utf-8-sig")
    assert "AuctionStructureScore" in scores.columns
    assert "FinalScore" not in scores.columns
    assert scores["AuctionStructureProductionApplied"].eq(False).all()
    summary = json.loads((tmp_path / "AuctionStructureBacktestSummary.json").read_text(encoding="utf-8"))
    assert summary["production_applied"] is False
    assert summary["scored_tickers"] == 1
