"""Command-line runner for the Auction / Structure shadow challenger.

Example::

    python -m institution_scanner.auction_structure_cli \
        --tickers 002961.SZ,601899.SH --benchmark 沪深300

The command writes separate diagnostic artifacts.  It never rewrites
``AllResults.csv`` or any production ranking/candidate view.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from config import BACKTEST_STOCK_COMMISSION_RATE, OUTPUT_DIR
from downloader import download_ticker, is_etf_ticker, normalize_ticker

from .auction_structure import (
    MODEL_PRODUCTION_APPLIED,
    MODEL_ROLE,
    MODEL_VERSION,
    AuctionStructureConfig,
    backtest_auction_structure,
    compute_auction_structure,
    snapshot_from_features,
    summarize_auction_backtest,
)

_BENCHMARKS = {
    "沪深300": "000300.SH",
    "中证500": "000905.SH",
    "创业板指": "399006.SZ",
}

_SNAPSHOT_COLUMNS = {
    "model_version": "AuctionStructureModelVersion",
    "model_role": "AuctionStructureModelRole",
    "production_applied": "AuctionStructureProductionApplied",
    "score": "AuctionStructureScore",
    "coverage": "AuctionStructureCoverage",
    "market_score": "AuctionMarketScore",
    "relative_strength_score": "AuctionRSScore",
    "trend_score": "AuctionTrendScore",
    "value_score": "AuctionValueScore",
    "structure_score": "AuctionStructureEventScore",
    "volume_score": "AuctionVolumeScore",
    "risk_score": "AuctionRiskScore",
    "market": "MarketState",
    "relative_strength": "RelativeStrengthState",
    "trend": "TrendState",
    "value": "ValueState",
    "structure": "StructureState",
    "volume": "VolumeBehavior",
    "risk": "RiskState",
    "candidate_setup": "CandidateSetup",
    "current_setup": "CurrentSetup",
    "active_plan": "ActivePlan",
    "last_plan": "LastPlan",
    "poc": "POC",
    "vah": "VAH",
    "val": "VAL",
    "avwap": "AVWAP",
    "value_migration_atr": "ValueMigrationATR",
    "rs20": "RS20",
    "rs60": "RS60",
    "atr": "ATR",
    "relative_volume": "RelativeVolume",
    "average_turnover20": "AverageTurnover20",
    "hard_block_reason": "HardBlockReason",
    "reward_risk": "ActiveRewardRisk",
    "entry_low": "ActiveEntryLow",
    "entry_high": "ActiveEntryHigh",
    "invalidation": "ActiveInvalidation",
    "target": "ActiveTarget",
}


def _ticker_rows(args: argparse.Namespace) -> list[tuple[str, str]]:
    sources = sum(
        bool(value)
        for value in (
            str(args.tickers or "").strip(),
            args.tickers_file,
            args.all_results,
        )
    )
    if sources != 1:
        raise ValueError("必须且只能指定 --tickers、--tickers-file 或 --all-results 之一")
    rows: list[tuple[str, str]] = []
    if args.tickers:
        rows.extend((item, "") for item in str(args.tickers).split(","))
    elif args.tickers_file:
        rows.extend((item, "") for item in Path(args.tickers_file).read_text(encoding="utf-8").splitlines())
    else:
        source = Path(args.output_dir) / "AllResults.csv"
        if not source.is_file():
            raise ValueError(f"未找到生产结果文件：{source}")
        frame = pd.read_csv(source, encoding="utf-8-sig", low_memory=False)
        if "Ticker" not in frame.columns:
            raise ValueError("AllResults.csv 缺少 Ticker 列")
        names = frame.get("Name", pd.Series("", index=frame.index)).fillna("")
        rows.extend(zip(frame["Ticker"].fillna(""), names, strict=False))

    deduplicated: dict[str, str] = {}
    for ticker, name in rows:
        normalised = normalize_ticker(str(ticker))
        if normalised:
            deduplicated.setdefault(normalised, str(name or "").strip())
    if not deduplicated:
        raise ValueError("影子模型标的列表为空")
    return list(deduplicated.items())


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, path)


def _atomic_json(payload: dict[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _clean_json(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _clean_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_json(item) for item in value]
    if isinstance(value, tuple):
        return [_clean_json(item) for item in value]
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(float(value)) else None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    return value


def run(args: argparse.Namespace) -> int:
    ticker_rows = _ticker_rows(args)
    benchmark_ticker = _BENCHMARKS[str(args.benchmark)]
    benchmark = download_ticker(
        benchmark_ticker,
        force=bool(args.force_download),
        source=args.data_source,
    )
    if benchmark is None or benchmark.empty:
        raise ValueError(f"基准 {args.benchmark}（{benchmark_ticker}）数据不可用")

    config = AuctionStructureConfig(minimum_turnover=float(args.minimum_turnover))
    snapshots: list[dict[str, object]] = []
    sample_frames: list[pd.DataFrame] = []
    errors: list[dict[str, str]] = []
    for ticker, name in ticker_rows:
        try:
            frame = download_ticker(
                ticker,
                force=bool(args.force_download),
                source=args.data_source,
            )
            if frame is None or frame.empty:
                raise ValueError("OHLCV 数据为空")
            frame = frame.sort_index().tail(int(args.max_bars))
            is_etf = is_etf_ticker(ticker)
            features = compute_auction_structure(
                frame,
                benchmark,
                ticker=ticker,
                name=name,
                is_etf=is_etf,
                config=config,
            )
            snapshot = snapshot_from_features(features)
            snapshot_payload = snapshot.to_dict()
            row = {
                "Ticker": ticker,
                "Name": name,
                "DataAsOf": pd.Timestamp(features.index[-1]).strftime("%Y-%m-%d"),
                **{exported: snapshot_payload[source] for source, exported in _SNAPSHOT_COLUMNS.items()},
            }
            snapshots.append(row)
            backtest = backtest_auction_structure(
                frame,
                benchmark,
                ticker=ticker,
                name=name,
                is_etf=is_etf,
                config=config,
                commission=float(args.commission),
                stamp_duty=float(args.stamp_duty),
                slippage=float(args.slippage),
                validation_ratio=float(args.validation_ratio),
                test_ratio=float(args.test_ratio),
                features=features,
            )
            if not backtest.samples.empty:
                sample_frames.append(backtest.samples)
        except (OSError, RuntimeError, TypeError, ValueError, KeyError, IndexError) as exc:
            errors.append({"ticker": ticker, "error": str(exc)})

    snapshot_frame = pd.DataFrame.from_records(snapshots)
    if not snapshot_frame.empty:
        snapshot_frame = snapshot_frame.sort_values(
            ["AuctionStructureScore", "Ticker"],
            ascending=[False, True],
            kind="stable",
        ).reset_index(drop=True)
        snapshot_frame.insert(0, "ShadowRank", np.arange(1, len(snapshot_frame) + 1))
    samples = pd.concat(sample_frames, ignore_index=True) if sample_frames else pd.DataFrame()
    if not samples.empty:
        samples = samples.sort_values(["SignalDate", "Ticker"], kind="stable").reset_index(drop=True)

    output_dir = Path(args.output_dir)
    _atomic_csv(snapshot_frame, output_dir / "AuctionStructureShadow.csv")
    _atomic_csv(samples, output_dir / "AuctionStructureBacktestSamples.csv")
    summary = summarize_auction_backtest(samples)
    summary.update(
        {
            "model_version": MODEL_VERSION,
            "model_role": MODEL_ROLE,
            "production_applied": MODEL_PRODUCTION_APPLIED,
            "benchmark": str(args.benchmark),
            "benchmark_ticker": benchmark_ticker,
            "requested_tickers": len(ticker_rows),
            "scored_tickers": len(snapshot_frame),
            "failed_tickers": len(errors),
            "errors": errors,
            "artifacts": {
                "scores": "AuctionStructureShadow.csv",
                "samples": "AuctionStructureBacktestSamples.csv",
                "summary": "AuctionStructureBacktestSummary.json",
            },
        }
    )
    _atomic_json(_clean_json(summary), output_dir / "AuctionStructureBacktestSummary.json")
    print(
        f"价值结构影子模型完成：{len(snapshot_frame)}/{len(ticker_rows)} 只，"
        f"回测样本 {len(samples)} 条；未改写生产评分。"
    )
    return 0 if snapshots else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auction-structure-shadow",
        description="价值结构影子评分与严格逐时点回测",
    )
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--tickers", type=str, default="")
    scope.add_argument("--tickers-file", type=Path, default=None)
    scope.add_argument("--all-results", action="store_true")
    parser.add_argument("--benchmark", choices=tuple(_BENCHMARKS), default="沪深300")
    parser.add_argument("--data-source", default="tickflow")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument(
        "--max-bars",
        type=int,
        default=900,
        help="每只标的最多使用的最近日K数量（默认 900，至少 300）",
    )
    parser.add_argument("--minimum-turnover", type=float, default=30_000_000.0)
    parser.add_argument(
        "--commission",
        type=float,
        default=BACKTEST_STOCK_COMMISSION_RATE,
    )
    parser.add_argument("--stamp-duty", type=float, default=0.0005)
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--validation-ratio", type=float, default=0.20)
    parser.add_argument("--test-ratio", type=float, default=0.20)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if int(args.max_bars) < 300:
            raise ValueError("--max-bars 不能小于 300")
        return run(args)
    except (OSError, RuntimeError, TypeError, ValueError, KeyError, IndexError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
