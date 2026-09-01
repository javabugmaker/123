"""Point-in-time rules-based backtest for the production scoring model.

Rebuilds the institutional-accumulation score (Setup/Trigger/Execution) on the
cached daily OHLCV ledger and compounds an equal-weight Top-K portfolio since
2022, benchmarked against CSI 300.

Integrity rules (deliberately conservative):
- Point-in-time: every score at rebalance date ``D`` uses only bars on or
  before ``D``.  All indicators are causal (rolling/ewm/cumsum); the only
  non-causal component, the volume-profile HVN term, is disabled for this
  backtest so no future information can leak into a score.
- No survivorship-free illusion: the universe is the current cache, which may
  omit securities that were delisted before the cache was captured.  That
  residual survivorship bias is documented, not hidden.
- Liquidity floor: a ticker must show a 20-day median CNY turnover above the
  configured floor at the rebalance date, otherwise it is not selectable.
- Gross-of-cost returns are compounded; a flat round-trip cost is deducted on
  each rebalance to approximate turnover drag.

This module is presentation-adjacent: production scoring, ranking, TradeReady
eligibility and order logic are not changed here.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pickle
import warnings
from concurrent.futures import ProcessPoolExecutor
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)

from config_core import OUTPUT_DIR  # noqa: E402

HISTORICAL_BACKTEST_VERSION = "2026-08-31-v1-pit-top30-rules-based"

CACHE_DIR_SUFFIX = Path("cache") / "v4-tickflow-forward-volume-shares"
BENCHMARK_TICKER = "000300.SH"
START_DATE = "2022-01-01"

TOP_K = 30
REBALANCE_DAYS = 20
WARMUP_BARS = 252
MIN_AMOUNT_MEDIAN_20D = 5_000_000.0  # CNY median 20d turnover floor
COST_PER_REBALANCE = 0.0025  # approximate full-turnover round-trip drag
MAX_WORKERS = 12

# Broad-market indices carry stock-like codes but are never a tradeable A-share
# security.  Excluding them stops the benchmark from being "selected" as a
# holding.  The cache is known to contain exactly these three index files.
_INDEX_TICKERS = frozenset(
    {
        "000300.SH",  # 沪深300
        "000905.SH",  # 中证500
        "399006.SZ",  # 创业板指
        # exhaustive guard set in case future caches add these:
        "000001.SH", "000002.SH", "000003.SH", "000010.SH", "000016.SH",
        "000688.SH", "000852.SH", "000906.SH", "000985.SH", "000998.SH",
        "399001.SZ", "399005.SZ", "399106.SZ", "399300.SZ", "399905.SZ",
        "399330.SZ", "399550.SZ", "399606.SZ", "399999.SZ",
        "899050.BJ", "899001.BJ",
    }
)


def _universe_dir() -> Path:
    return Path(OUTPUT_DIR).parent / CACHE_DIR_SUFFIX


def list_universe() -> list[str]:
    directory = _universe_dir()
    if not directory.is_dir():
        return []
    result: set[str] = set()
    for path in directory.glob("*.parquet"):
        ticker = path.stem
        if ticker in _INDEX_TICKERS:
            continue
        if not ticker.endswith((".SH", ".SZ")):
            continue
        result.add(ticker)
    return sorted(result)


def _load_benchmark() -> pd.Series:
    path = _universe_dir() / f"{BENCHMARK_TICKER}.parquet"
    frame = pd.read_parquet(path)
    return pd.to_numeric(frame["Close"], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )


def _trading_days(benchmark: pd.Series, start: str) -> pd.DatetimeIndex:
    days = benchmark.index
    if not isinstance(days, pd.DatetimeIndex):
        days = pd.to_datetime(days)
    days = days[days >= pd.Timestamp(start)]
    return days


def _worker(
    payload: tuple[list[str], np.ndarray, np.ndarray, np.ndarray],
) -> tuple[list[tuple[int, str, float]], dict[str, np.ndarray]]:
    """Score ``chunk`` tickers at each rebalance date; return scores + aligned returns."""
    import indicators as _indicators
    from institution_scanner.backtest_score_vectorized import final_score_series

    _indicators.ENABLE_VOLUME_PROFILE = False
    logging.getLogger("institution_scanner.score").setLevel(logging.CRITICAL)

    tickers, trading_days, rebalance_dates_np, rebalance_positions_np = payload
    universe_dir = _universe_dir()
    scores: list[tuple[int, str, float]] = []
    returns: dict[str, np.ndarray] = {}

    for ticker in tickers:
        try:
            frame = pd.read_parquet(universe_dir / f"{ticker}.parquet")
        except (OSError, ValueError) as exc:
            logging.getLogger("historical_backtest").warning(
                "skip %s: %s", ticker, exc
            )
            continue
        frame = frame.loc[~frame.index.duplicated(keep="last")].sort_index()
        close = pd.to_numeric(frame["Close"], errors="coerce")
        if len(close) < WARMUP_BARS + 1:
            continue

        _indicators.compute_all_indicators(frame)
        score_series = final_score_series(frame).astype(np.float64)

        # Aligned daily returns (NaN where the ticker did not trade that day).
        ret_series = close.pct_change()
        aligned = ret_series.reindex(trading_days).to_numpy(dtype=np.float32)
        returns[ticker] = aligned

        index_values = frame.index.values
        amount = pd.to_numeric(
            frame.get("Amount", pd.Series(np.nan, index=frame.index)),
            errors="coerce",
        )
        amount_med = amount.rolling(20, min_periods=10).median().to_numpy(dtype=np.float64)
        close_np = close.to_numpy(dtype=np.float64)

        for local_i, day_ns in enumerate(rebalance_dates_np):
            di = int(rebalance_positions_np[local_i])
            position = int(np.searchsorted(index_values, day_ns, side="right")) - 1
            if position + 1 < WARMUP_BARS:
                continue
            med = amount_med[position]
            if not np.isfinite(med) or med < MIN_AMOUNT_MEDIAN_20D:
                continue
            if not np.isfinite(close_np[position]) or close_np[position] <= 0:
                continue
            value = float(score_series[position])
            if np.isfinite(value) and value > 0:
                scores.append((di, ticker, value))

    return scores, returns


def _compile_returns(
    returns: dict[str, np.ndarray],
    ticker_ids: dict[str, int],
    n_days: int,
) -> np.ndarray:
    matrix = np.full((len(ticker_ids), n_days), np.nan, dtype=np.float32)
    for ticker, aligned in returns.items():
        if ticker not in ticker_ids or len(aligned) != n_days:
            continue
        matrix[ticker_ids[ticker]] = aligned
    return matrix


def _drawdown(nav: np.ndarray) -> np.ndarray:
    peak = np.maximum.accumulate(nav)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = nav / peak - 1.0
    return dd * 100.0


def _cum_return(nav: np.ndarray) -> float:
    return float(nav[-1] / nav[0] - 1.0) * 100.0 if nav[0] > 0 else float("nan")


def _max_drawdown(dd: np.ndarray) -> float:
    return float(np.nanmin(dd)) if len(dd) else float("nan")


def _sharpe(daily_returns: np.ndarray) -> float:
    clean = daily_returns[~np.isnan(daily_returns)]
    if len(clean) < 2 or float(np.std(clean)) == 0.0:
        return float("nan")
    return float(np.mean(clean) / np.std(clean) * np.sqrt(252.0))


def _cagr(nav: np.ndarray, n_days: int) -> float:
    if nav[0] <= 0 or n_days <= 0:
        return float("nan")
    total = nav[-1] / nav[0]
    years = n_days / 252.0
    if total <= 0 or years <= 0:
        return float("nan")
    return (total ** (1.0 / years) - 1.0) * 100.0


def _checkpoint_path(output_dir: Path) -> Path:
    return Path(output_dir) / ".HistoricalBacktest.checkpoint.pkl"


def _write_checkpoint(
    path: Path,
    *,
    tickers: list[str],
    n_days: int,
    top_k: int,
    rebalance_days: int,
    scores: list[tuple[int, str, float]],
    returns: dict[str, np.ndarray],
) -> None:
    """Persist the expensive scoring pass so a post-scoring crash is cheap to resume."""
    payload = {
        "tickers": tickers,
        "n_days": n_days,
        "top_k": top_k,
        "rebalance_days": rebalance_days,
        "scores": scores,
        "returns": returns,
    }
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        temporary.write_bytes(pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL))
        os.replace(temporary, path)
    except OSError:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def _load_checkpoint(
    path: Path,
    *,
    tickers: list[str],
    n_days: int,
    top_k: int,
    rebalance_days: int,
) -> tuple[list[tuple[int, str, float]], dict[str, np.ndarray]]:
    try:
        payload = pickle.loads(path.read_bytes())
    except (OSError, pickle.PickleError, EOFError, ValueError):
        return [], {}
    if not (
        payload.get("tickers") == tickers
        and payload.get("n_days") == n_days
        and payload.get("top_k") == top_k
        and payload.get("rebalance_days") == rebalance_days
    ):
        return [], {}
    return payload.get("scores", []), payload.get("returns", {})


def run_backtest(
    *,
    output_dir: Path = OUTPUT_DIR,
    start: str = START_DATE,
    top_k: int = TOP_K,
    rebalance_days: int = REBALANCE_DAYS,
    max_workers: int = MAX_WORKERS,
) -> dict[str, Any]:
    output_dir = Path(output_dir)
    benchmark = _load_benchmark()
    trading_days = _trading_days(benchmark, start)
    if len(trading_days) == 0:
        raise ValueError("no trading days available after start date")

    day_index = pd.DatetimeIndex(trading_days)
    n_days = len(day_index)
    rebalance_indices = list(range(0, n_days, rebalance_days))
    rebalance_dates_np = day_index[rebalance_indices].values
    rebalance_positions_np = np.asarray(rebalance_indices, dtype=np.int64)
    benchmark_close = benchmark.reindex(day_index).astype(np.float64)
    benchmark_ret = benchmark_close.pct_change().to_numpy(dtype=np.float64)

    tickers = list_universe()

    scores: list[tuple[int, str, float]] = []
    returns: dict[str, np.ndarray] = {}
    chunks = np.array_split(np.asarray(tickers), max(1, max_workers))
    payloads = [
        (list(chunk), day_index, rebalance_dates_np, rebalance_positions_np)
        for chunk in chunks
        if len(chunk)
    ]

    logging.getLogger("historical_backtest").info(
        "running point-in-time backtest: %d tickers, %d trading days, "
        "%d rebalances, top%d / %dd",
        len(tickers),
        n_days,
        len(rebalance_indices),
        top_k,
        rebalance_days,
    )

    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        for chunk_scores, chunk_returns in pool.map(_worker, payloads):
            scores.extend(chunk_scores)
            returns.update(chunk_returns)

    if not scores:
        raise RuntimeError("no scores produced — check cache and universe filters")

    ticker_ids = {ticker: i for i, ticker in enumerate(tickers)}
    returns_matrix = _compile_returns(returns, ticker_ids, n_days)

    # Group scores by rebalance date and select the Top-K each period.
    by_date: dict[int, list[tuple[float, int]]] = {}
    for di, ticker, value in scores:
        by_date.setdefault(di, []).append((value, ticker_ids[ticker]))

    portfolio_ret = np.zeros(n_days, dtype=np.float64)
    holdings_log: list[dict[str, Any]] = []

    for idx, start_i in enumerate(rebalance_indices):
        end_i = rebalance_indices[idx + 1] if idx + 1 < len(rebalance_indices) else n_days
        ranked = sorted(by_date.get(start_i, ()), reverse=True)
        if not ranked:
            continue
        chosen_ids = [tid for _value, tid in ranked[:top_k]]
        holdings_log.append(
            {
                "date": str(day_index[start_i].date()),
                "n": len(chosen_ids),
                "tickers": ",".join(tickers[tid] for tid in chosen_ids),
            }
        )
        for t in range(start_i + 1, end_i):
            window = returns_matrix[chosen_ids, t]
            finite = window[~np.isnan(window)]
            if len(finite) == 0:
                continue
            portfolio_ret[t] = float(np.mean(finite))
        # Approximate round-trip turnover drag charged on the rebalance session.
        if idx > 0:
            portfolio_ret[start_i] -= COST_PER_REBALANCE

    strategy_nav = np.cumprod(1.0 + portfolio_ret)
    benchmark_nav = np.cumprod(np.nan_to_num(1.0 + benchmark_ret, nan=1.0))

    strategy_dd = _drawdown(strategy_nav)
    benchmark_dd = _drawdown(benchmark_nav)

    # Annual aggregation.
    years = day_index.year
    annual: dict[str, dict[str, float]] = {}
    for year in sorted(set(int(y) for y in years)):
        mask = years == year
        sub = portfolio_ret[mask]
        sub_bench = benchmark_ret[mask]
        sub_nav = (1.0 + sub).cumprod()
        sub_nav = np.concatenate(([1.0], sub_nav))
        annual[str(year)] = {
            "return_pct": round(float((sub_nav[-1] - 1.0) * 100.0), 2),
            "max_drawdown_pct": round(_max_drawdown(_drawdown(sub_nav)), 2),
            "benchmark_return_pct": round(
                float((np.nan_to_num(1.0 + sub_bench, nan=1.0).cumprod()[-1] - 1.0) * 100.0),
                2,
            ),
            "trading_days": int(mask.sum()),
        }

    rows = [
        {
            "Date": str(day_index[t].date()),
            "StrategyNAV": round(float(strategy_nav[t]), 6),
            "BenchmarkNAV": round(float(benchmark_nav[t]), 6),
            "StrategyDrawdown": round(float(strategy_dd[t]), 4),
            "BenchmarkDrawdown": round(float(benchmark_dd[t]), 4),
        }
        for t in range(n_days)
    ]

    summary = {
        "start": str(day_index[0].date()),
        "end": str(day_index[-1].date()),
        "trading_days": n_days,
        "rebalance_count": len(holdings_log),
        "top_k": top_k,
        "rebalance_days": rebalance_days,
        "strategy_final_nav": round(float(strategy_nav[-1]), 4),
        "benchmark_final_nav": round(float(benchmark_nav[-1]), 4),
        "strategy_total_return_pct": round(_cum_return(strategy_nav), 2),
        "benchmark_total_return_pct": round(_cum_return(benchmark_nav), 2),
        "strategy_cagr_pct": round(_cagr(strategy_nav, n_days), 2),
        "benchmark_cagr_pct": round(_cagr(benchmark_nav, n_days), 2),
        "strategy_max_drawdown_pct": round(_max_drawdown(strategy_dd), 2),
        "benchmark_max_drawdown_pct": round(_max_drawdown(benchmark_dd), 2),
        "strategy_sharpe": round(_sharpe(portfolio_ret), 3),
        "benchmark_sharpe": round(_sharpe(benchmark_ret), 3),
        "annual": annual,
        "universe_tickers": len(tickers),
        "volume_profile_enabled": False,
        "cost_per_rebalance_pct": COST_PER_REBALANCE * 100.0,
    }

    payload = {
        "version": HISTORICAL_BACKTEST_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": {
            "start": start,
            "top_k": top_k,
            "rebalance_days": rebalance_days,
            "warmup_bars": WARMUP_BARS,
            "benchmark": BENCHMARK_TICKER,
            "min_amount_median_20d": MIN_AMOUNT_MEDIAN_20D,
            "cost_per_rebalance": COST_PER_REBALANCE,
            "volume_profile_enabled": False,
        },
        "summary": summary,
        "rows": rows,
    }

    _write_payload(output_dir, payload, summary)
    return payload


def _write_payload(
    output_dir: Path,
    payload: dict[str, Any],
    summary: dict[str, Any],
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "HistoricalBacktest.json"
    csv_path = output_dir / "HistoricalBacktest.csv"

    frame = pd.DataFrame(payload["rows"])
    csv_tmp = csv_path.with_name(f".{csv_path.name}.tmp")
    json_tmp = json_path.with_name(f".{json_path.name}.tmp")
    try:
        frame.to_csv(csv_tmp, index=False, encoding="utf-8-sig")
        json_tmp.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(csv_tmp, csv_path)
        os.replace(json_tmp, json_path)
    finally:
        for tmp in (csv_tmp, json_tmp):
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    return json_path, csv_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Point-in-time rules-based backtest")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--start", default=START_DATE)
    parser.add_argument("--top-k", type=int, default=TOP_K)
    parser.add_argument("--rebalance-days", type=int, default=REBALANCE_DAYS)
    parser.add_argument("--workers", type=int, default=MAX_WORKERS)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    result = run_backtest(
        output_dir=args.output_dir,
        start=args.start,
        top_k=args.top_k,
        rebalance_days=args.rebalance_days,
        max_workers=args.workers,
    )
    summary = result["summary"]
    print(
        "backtest done: %s -> %s | strategy %.4f (%+.1f%%) | benchmark %.4f (%+.1f%%) | "
        "maxDD %+.1f%% | Sharpe %.2f",
        summary["start"],
        summary["end"],
        summary["strategy_final_nav"],
        summary["strategy_total_return_pct"],
        summary["benchmark_final_nav"],
        summary["benchmark_total_return_pct"],
        summary["strategy_max_drawdown_pct"],
        summary["strategy_sharpe"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())