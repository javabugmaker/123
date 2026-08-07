from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one regex match, found {count}")
    return updated


PERFORMANCE_CACHE = r'''from __future__ import annotations

"""Persistent compute caches used by scanner and historical backtests.

The cache keys deliberately include the raw market-cache file signature and the
scoring version.  A TickFlow refresh/rebase or a scoring change therefore
invalidates stale derived data automatically instead of mixing generations.
"""

import gzip
import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from config import CACHE_DIR, SCORING_VERSION

INDICATOR_CACHE_VERSION = "v2"
BACKTEST_CACHE_VERSION = "v4"
INDICATOR_CACHE_DIR = CACHE_DIR / f"_indicators_{INDICATOR_CACHE_VERSION}"
BACKTEST_CACHE_DIR = CACHE_DIR / f"_backtest_{BACKTEST_CACHE_VERSION}"


def _safe_stem(ticker: str) -> str:
    value = str(ticker).strip().upper()
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).rstrip(" .")
    return safe or "ticker"


def file_signature(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        stat = path.stat()
    except OSError:
        return ""
    payload = f"{path.name}|{stat.st_size}|{stat.st_mtime_ns}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _frame_identity(df: pd.DataFrame) -> dict[str, Any]:
    if df is None or df.empty:
        return {"rows": 0, "first": "", "last": ""}
    index = pd.DatetimeIndex(pd.to_datetime(df.index, errors="coerce")).dropna()
    first = str(index.min()) if len(index) else ""
    last = str(index.max()) if len(index) else ""
    close = pd.to_numeric(df.get("Close", pd.Series(dtype=float)), errors="coerce")
    sample_values: list[float] = []
    if len(close):
        positions = sorted({0, len(close) // 4, len(close) // 2, len(close) * 3 // 4, len(close) - 1})
        for position in positions:
            try:
                value = float(close.iloc[position])
            except (IndexError, TypeError, ValueError):
                value = float("nan")
            sample_values.append(round(value, 8) if np.isfinite(value) else 0.0)
    return {
        "rows": int(len(df)),
        "first": first,
        "last": last,
        "sample_close": sample_values,
    }


def data_signature(df: pd.DataFrame, source_path: Path | None = None) -> str:
    payload = {
        "file": file_signature(source_path),
        "frame": _frame_identity(df),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, suffix=".json", delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".parquet", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_parquet(temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_or_compute_indicators(
    ticker: str,
    frame: pd.DataFrame,
    compute_fn: Callable[[pd.DataFrame], pd.DataFrame],
    *,
    source_path: Path | None = None,
    enabled: bool = True,
) -> tuple[pd.DataFrame, bool]:
    """Return enriched indicators and whether a persistent cache was reused."""
    if frame is None or frame.empty:
        return frame, False
    if not enabled or source_path is None or not source_path.exists():
        return compute_fn(frame.copy()), False

    signature = data_signature(frame, source_path)
    stem = _safe_stem(ticker)
    data_path = INDICATOR_CACHE_DIR / f"{stem}.parquet"
    meta_path = INDICATOR_CACHE_DIR / f"{stem}.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        if (
            data_path.exists()
            and meta.get("version") == INDICATOR_CACHE_VERSION
            and meta.get("scoring_version") == SCORING_VERSION
            and meta.get("signature") == signature
        ):
            cached = pd.read_parquet(data_path)
            cached.index = pd.to_datetime(cached.index, errors="coerce")
            if len(cached) == len(frame) and not cached.empty:
                source_last = pd.Timestamp(pd.to_datetime(frame.index, errors="coerce").max())
                cache_last = pd.Timestamp(cached.index.max())
                if source_last == cache_last:
                    return cached, True
    except (OSError, ValueError, TypeError, json.JSONDecodeError, ImportError):
        pass

    enriched = compute_fn(frame.copy())
    try:
        _atomic_parquet(data_path, enriched)
        _atomic_json(
            meta_path,
            {
                "version": INDICATOR_CACHE_VERSION,
                "scoring_version": SCORING_VERSION,
                "signature": signature,
                **_frame_identity(frame),
            },
        )
    except (OSError, ValueError, TypeError, ImportError):
        pass
    return enriched, False


def backtest_cache_key(payload: dict[str, Any]) -> str:
    normalized = {
        "version": BACKTEST_CACHE_VERSION,
        "scoring_version": SCORING_VERSION,
        **payload,
    }
    raw = json.dumps(normalized, sort_keys=True, ensure_ascii=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_backtest_cache(ticker: str, key: str) -> list[dict[str, Any]] | None:
    path = BACKTEST_CACHE_DIR / f"{_safe_stem(ticker)}.json.gz"
    if not key or not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("key") != key or not isinstance(payload.get("samples"), list):
            return None
        return list(payload["samples"])
    except (OSError, EOFError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _json_default(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value)!r}")


def save_backtest_cache(ticker: str, key: str, samples: list[dict[str, Any]]) -> None:
    if not key:
        return
    BACKTEST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKTEST_CACHE_DIR / f"{_safe_stem(ticker)}.json.gz"
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".json.gz", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=5) as stream:
            json.dump({"key": key, "samples": samples}, stream, ensure_ascii=False, default=_json_default, separators=(",", ":"))
        temporary.replace(path)
    except (OSError, TypeError, ValueError):
        temporary.unlink(missing_ok=True)
        return
    finally:
        temporary.unlink(missing_ok=True)
'''


# ---------------------------------------------------------------------------
# config.py
# ---------------------------------------------------------------------------
config = read("config.py")
config = replace_once(
    config,
    'SCORING_VERSION: str = "2026-08-08-v12-tickflow-free-akshare-fundamentals"',
    'SCORING_VERSION: str = "2026-08-08-v13-performance-cache-process-backtest"',
    "config scoring version",
)
config = replace_once(
    config,
    'BACKTEST_OUTCOME_HORIZON_DAYS: Final[int] = 60\n',
    'BACKTEST_OUTCOME_HORIZON_DAYS: Final[int] = 60\n'
    '# Performance: score functions need at most 504 historical rows once indicators\n'
    '# have been precomputed.  Keeping this bound avoids repeatedly scanning 10 years.\n'
    'BACKTEST_SCORE_WINDOW_BARS: Final[int] = 504\n'
    'BACKTEST_MAX_PROCESSES: Final[int] = 8\n'
    'BACKTEST_PROCESS_MIN_TICKERS: Final[int] = 100\n'
    'BACKTEST_CHUNK_SIZE: Final[int] = 8\n'
    'BACKTEST_PROGRESS_INTERVAL: Final[int] = 25\n'
    'BACKTEST_CACHE_ENABLED: Final[bool] = True\n'
    'INDICATOR_CACHE_ENABLED: Final[bool] = True\n'
    'CACHE_READ_THREADS: Final[int] = 8\n',
    "config performance constants",
)
write("config.py", config)
write("performance_cache.py", PERFORMANCE_CACHE)


# ---------------------------------------------------------------------------
# analytics.py
# ---------------------------------------------------------------------------
analytics = read("analytics.py")
analytics = replace_once(
    analytics,
    'import tempfile\nfrom concurrent.futures import ThreadPoolExecutor, as_completed',
    'import tempfile\nimport time\nfrom concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed',
    "analytics concurrent imports",
)
analytics = replace_once(
    analytics,
    '    BACKTEST_OUTCOME_HORIZON_DAYS,\n    BACKTEST_SIGNAL_COOLDOWN_DAYS,',
    '    BACKTEST_OUTCOME_HORIZON_DAYS,\n'
    '    BACKTEST_SIGNAL_COOLDOWN_DAYS,\n'
    '    BACKTEST_SCORE_WINDOW_BARS,\n'
    '    BACKTEST_MAX_PROCESSES,\n'
    '    BACKTEST_PROCESS_MIN_TICKERS,\n'
    '    BACKTEST_CHUNK_SIZE,\n'
    '    BACKTEST_PROGRESS_INTERVAL,\n'
    '    BACKTEST_CACHE_ENABLED,\n'
    '    INDICATOR_CACHE_ENABLED,',
    "analytics config imports",
)
analytics = replace_once(
    analytics,
    'from downloader import (\n    _is_a_share_market_closed,\n    _load_cache,',
    'from downloader import (\n    _is_a_share_market_closed,\n    _cache_path,\n    _load_cache,',
    "analytics downloader cache path",
)
analytics = replace_once(
    analytics,
    'from indicators import compute_all_indicators, compute_volume_profile\n',
    'from indicators import compute_all_indicators, compute_volume_profile\n'
    'from performance_cache import (\n'
    '    backtest_cache_key,\n'
    '    file_signature,\n'
    '    load_backtest_cache,\n'
    '    load_or_compute_indicators,\n'
    '    save_backtest_cache,\n'
    ')\n',
    "analytics performance cache import",
)
analytics = replace_once(
    analytics,
    'class BacktestSummary:\n    samples: int = 0\n    ticker_count: int = 0\n',
    'class BacktestSummary:\n'
    '    samples: int = 0\n'
    '    ticker_count: int = 0\n'
    '    cache_hits: int = 0\n'
    '    elapsed_seconds: float = 0.0\n'
    '    worker_count: int = 0\n'
    '    engine: str = "sequential"\n',
    "backtest summary performance fields",
)

SIGNAL_BLOCK = r'''def _backtest_scoring_window(enriched: pd.DataFrame, index: int) -> pd.DataFrame:
    """Return only the history score_ticker can actually consume.

    Indicators are already computed on the chronological full series, so the
    cumulative OBV/A-D values keep their original levels.  The 504-bar bound is
    sufficient for every current scoring lookback and preserves the saturated
    days-below-MA200 logic while cutting repeated DataFrame work sharply.
    """
    end = int(index) + 1
    start = max(0, end - int(BACKTEST_SCORE_WINDOW_BARS))
    historical = enriched.iloc[start:end].copy(deep=False)
    if ENABLE_VOLUME_PROFILE:
        # Volume Profile is end-point dependent and compute_all_indicators writes
        # the latest profile across the column, so recompute it for this date.
        historical = historical.drop(
            columns=[
                "VP_HVN_Center",
                "DistToHVN_Pct",
                "Above_HVN",
                "VP_LVN_Center",
                "DistToLVN_Pct",
            ],
            errors="ignore",
        )
        try:
            compute_volume_profile(historical)
        except (ArithmeticError, TypeError, ValueError):
            logger.debug("Historical volume profile failed.", exc_info=True)
    return historical


def _signal_evaluations(
    enriched: pd.DataFrame,
    cooldown: int = BACKTEST_SIGNAL_COOLDOWN_DAYS,
    is_etf: bool = False,
) -> list[tuple[int, float, str]]:
    """Find actionable historical entries and retain their already-computed score.

    The former implementation scored a valid historical point once while
    discovering it and then scored the same prefix again in _backtest_one_ticker.
    Returning score/signal together removes that duplicate hot path.
    """
    live_columns = {"High", "Low", "MA20", "MA50", "ATR14"}
    if len(enriched) < 252:
        if not live_columns.issubset(enriched.columns):
            return [
                (index, np.nan, "UNKNOWN")
                for index in _legacy_signal_points(enriched, max(1, int(cooldown)))
            ]
        return []
    if not live_columns.issubset(enriched.columns):
        return [
            (index, np.nan, "UNKNOWN")
            for index in _legacy_signal_points(enriched, max(1, int(cooldown)))
        ]

    cooldown = max(1, int(cooldown))
    close = pd.to_numeric(enriched["Close"], errors="coerce")
    high = pd.to_numeric(enriched["High"], errors="coerce")
    low = pd.to_numeric(enriched["Low"], errors="coerce")
    ma20 = pd.to_numeric(enriched["MA20"], errors="coerce")
    ma50 = pd.to_numeric(enriched["MA50"], errors="coerce")
    atr = pd.to_numeric(enriched["ATR14"], errors="coerce")
    support = low.rolling(20, min_periods=20).min()
    resistance = high.shift(1).rolling(20, min_periods=20).max()
    effective_atr = atr.where(atr.gt(0), close * 0.03)
    near_support = close.le(support + effective_atr * 1.5)
    five_day_up = close.ge(close.shift(5))
    trend_candidate = close.gt(ma20) & (ma20.ge(ma50) | five_day_up | near_support)
    broad_candidate = (trend_candidate | near_support | close.gt(resistance)).fillna(False)
    candidates = np.flatnonzero(broad_candidate.to_numpy(dtype=bool))

    last_signal = -cooldown
    evaluations: list[tuple[int, float, str]] = []
    for index in candidates:
        if index < 251:
            continue
        if index >= len(enriched) - BACKTEST_OUTCOME_HORIZON_DAYS:
            continue
        if index - last_signal < cooldown:
            continue
        historical = _backtest_scoring_window(enriched, int(index))
        historical_score = score_ticker(historical, is_etf=is_etf)
        historical_entry = entry_point(
            historical,
            breakout=_finite_float(getattr(historical_score, "breakout_score", np.nan), np.nan),
            volume_score=_finite_float(getattr(historical_score, "volume", np.nan), np.nan),
            value_trap_risk_value=_finite_float(getattr(historical_score, "value_trap_risk", np.nan), np.nan),
        )
        signal = str(historical_entry.get("signal", "AVOID")).upper()
        if signal not in _BACKTEST_ACTIONABLE_SIGNALS:
            continue
        final_score = _finite_float(getattr(historical_score, "final_score", np.nan), np.nan)
        if not np.isfinite(final_score):
            final_score = _finite_float(getattr(historical_score, "total", np.nan), 0.0)
        evaluations.append((int(index), float(final_score), signal))
        last_signal = int(index)
    return evaluations


def _signal_points(
    enriched: pd.DataFrame,
    cooldown: int = BACKTEST_SIGNAL_COOLDOWN_DAYS,
    is_etf: bool = False,
) -> list[int]:
    """Compatibility wrapper returning only historical signal indexes."""
    return [
        index
        for index, _score, _signal in _signal_evaluations(
            enriched, cooldown=cooldown, is_etf=is_etf
        )
    ]
'''
analytics = regex_once(
    analytics,
    r'def _signal_points\(.*?(?=\ndef _historical_entry_signal)',
    SIGNAL_BLOCK.rstrip() + "\n",
    "analytics signal evaluation block",
)

BACKTEST_ONE = r'''def _backtest_one_ticker(
    ticker: str,
    source: str,
    benchmark_frame: pd.DataFrame | None = None,
    commission: float = 0.0003,
    stamp_duty: float = 0.0005,
    slippage: float = 0.001,
    split_dates: tuple[pd.Timestamp | None, pd.Timestamp | None] = (None, None),
) -> list[dict[str, Any]]:
    frame = _load_cache(ticker, source)
    if frame is None or len(frame) < 300:
        return []
    raw_path = _cache_path(ticker, source)
    enriched, _indicator_cache_hit = load_or_compute_indicators(
        ticker,
        frame,
        compute_all_indicators,
        source_path=raw_path if raw_path.exists() else None,
        enabled=INDICATOR_CACHE_ENABLED,
    )
    is_etf = is_etf_ticker(str(ticker))
    evaluations = _signal_evaluations(enriched, is_etf=is_etf)
    if not evaluations:
        return []
    evaluation_map = {
        index: (score, signal) for index, score, signal in evaluations
    }

    opens = enriched["Open"].to_numpy(dtype=float) if "Open" in enriched else np.full(len(enriched), np.nan)
    lows = enriched["Low"].to_numpy(dtype=float) if "Low" in enriched else np.full(len(enriched), np.nan)
    closes = enriched["Close"].to_numpy(dtype=float)
    highs = enriched["High"].to_numpy(dtype=float) if "High" in enriched else closes.copy()
    outcome_horizon = max(60, int(BACKTEST_OUTCOME_HORIZON_DAYS))
    valid_points: list[int] = []
    for index, _score, _signal in evaluations:
        entry_index = index + 1
        if entry_index >= len(enriched):
            continue
        if not np.isfinite(opens[entry_index]) or opens[entry_index] <= 0:
            continue
        if (
            entry_index + outcome_horizon >= len(enriched)
            or not np.isfinite(closes[entry_index + 20])
            or not np.isfinite(closes[entry_index + outcome_horizon])
        ):
            continue
        if np.any(~np.isfinite(highs[entry_index : entry_index + outcome_horizon + 1])) or np.any(highs[entry_index : entry_index + outcome_horizon + 1] <= 0):
            continue
        if np.any(~np.isfinite(lows[entry_index : entry_index + outcome_horizon + 1])) or np.any(lows[entry_index : entry_index + outcome_horizon + 1] <= 0):
            continue
        valid_points.append(index)
    if not valid_points:
        return []

    benchmark_close = None
    if benchmark_frame is not None and not benchmark_frame.empty:
        benchmark_close = benchmark_frame["Close"].astype(float).sort_index()
    validation_end, test_start = split_dates
    samples: list[dict[str, Any]] = []
    previous_sample_index: int | None = None
    for index in valid_points:
        signal_date = pd.Timestamp(enriched.index[index])
        entry_index = index + 1
        entry_date = pd.Timestamp(enriched.index[entry_index])
        entry_price = opens[entry_index]
        future20 = closes[entry_index + 20]
        future60 = closes[entry_index + outcome_horizon]
        benchmark_returns: dict[int, float] = {20: np.nan, 60: np.nan}
        if benchmark_close is not None:
            start_date = benchmark_close.index.asof(entry_date)
            for period in (20, 60):
                future_date = pd.Timestamp(enriched.index[entry_index + period])
                end_date = benchmark_close.index.asof(future_date)
                if (
                    pd.notna(start_date)
                    and pd.notna(end_date)
                    and end_date == future_date
                    and benchmark_close.loc[start_date] > 0
                ):
                    benchmark_returns[period] = (
                        benchmark_close.loc[end_date] / benchmark_close.loc[start_date] - 1
                    ) * 100
        cost_percent = (commission * 2 + slippage * 2 + (0.0 if is_etf else stamp_duty)) * 100
        prices20 = np.concatenate(([entry_price], closes[entry_index : entry_index + 21]))
        prices60 = np.concatenate(([entry_price], closes[entry_index : entry_index + outcome_horizon + 1]))
        lows20 = np.concatenate(([entry_price], lows[entry_index : entry_index + 21]))
        lows60 = np.concatenate(([entry_price], lows[entry_index : entry_index + outcome_horizon + 1]))
        drawdown20 = float(((lows20 / np.maximum.accumulate(prices20) - 1).min()) * 100)
        drawdown60 = float(((lows60 / np.maximum.accumulate(prices60) - 1).min()) * 100)
        if test_start is not None and entry_date >= test_start:
            split = "test"
        elif validation_end is not None and entry_date >= validation_end:
            split = "validation"
        else:
            split = "train"
        spacing = outcome_horizon if previous_sample_index is None else max(1, index - previous_sample_index)
        sample_weight = min(1.0, spacing / float(outcome_horizon))
        historical_score, historical_signal = evaluation_map[index]
        samples.append(
            {
                "ticker": ticker,
                "entry_signal": historical_signal,
                "signal_date": signal_date.strftime("%Y-%m-%d"),
                "entry_date": entry_date.strftime("%Y-%m-%d"),
                "entry_price": float(entry_price),
                "return20": (future20 / entry_price - 1) * 100,
                "return60": (future60 / entry_price - 1) * 100,
                "benchmark_return20": benchmark_returns[20],
                "benchmark_return60": benchmark_returns[60],
                "net_return20": (future20 / entry_price - 1) * 100 - cost_percent,
                "net_return60": (future60 / entry_price - 1) * 100 - cost_percent,
                "drawdown20": drawdown20,
                "drawdown60": drawdown60,
                "score": historical_score,
                "split": split,
                "sample_weight": round(sample_weight, 4),
            }
        )
        previous_sample_index = index
    return samples


def _backtest_one_ticker_cached(
    ticker: str,
    source: str,
    benchmark_frame: pd.DataFrame | None,
    commission: float,
    stamp_duty: float,
    slippage: float,
    split_dates: tuple[pd.Timestamp | None, pd.Timestamp | None],
    benchmark_signature: str,
) -> tuple[list[dict[str, Any]], bool]:
    raw_path = _cache_path(ticker, source)
    price_signature = file_signature(raw_path)
    cache_key = ""
    if price_signature:
        cache_key = backtest_cache_key(
            {
                "ticker": str(ticker),
                "source": str(source),
                "price_signature": price_signature,
                "benchmark_signature": benchmark_signature,
                "commission": float(commission),
                "stamp_duty": float(stamp_duty),
                "slippage": float(slippage),
                "split_dates": [
                    value.isoformat() if value is not None else None
                    for value in split_dates
                ],
                "cooldown": int(BACKTEST_SIGNAL_COOLDOWN_DAYS),
                "horizon": int(BACKTEST_OUTCOME_HORIZON_DAYS),
                "score_window": int(BACKTEST_SCORE_WINDOW_BARS),
            }
        )
    if BACKTEST_CACHE_ENABLED and cache_key:
        cached = load_backtest_cache(ticker, cache_key)
        if cached is not None:
            return cached, True
    samples = _backtest_one_ticker(
        ticker,
        source,
        benchmark_frame,
        commission,
        stamp_duty,
        slippage,
        split_dates,
    )
    if BACKTEST_CACHE_ENABLED and cache_key:
        save_backtest_cache(ticker, cache_key, samples)
    return samples, False
'''
analytics = regex_once(
    analytics,
    r'def _backtest_one_ticker\(.*?(?=\ndef _backtest_evidence)',
    BACKTEST_ONE.rstrip() + "\n",
    "analytics optimized ticker backtest",
)

WORKER_BLOCK = r'''
_BACKTEST_WORKER_CONTEXT: dict[str, Any] = {}


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _init_backtest_worker(
    source: str,
    benchmark: str,
    commission: float,
    stamp_duty: float,
    slippage: float,
    split_dates: tuple[pd.Timestamp | None, pd.Timestamp | None],
    benchmark_signature: str,
) -> None:
    global _BACKTEST_WORKER_CONTEXT
    benchmark_frame = _load_cache(BENCHMARKS[benchmark], source)
    _BACKTEST_WORKER_CONTEXT = {
        "source": source,
        "benchmark_frame": benchmark_frame,
        "commission": commission,
        "stamp_duty": stamp_duty,
        "slippage": slippage,
        "split_dates": split_dates,
        "benchmark_signature": benchmark_signature,
    }


def _backtest_chunk_worker(
    tickers: list[str],
) -> tuple[list[dict[str, Any]], int, list[tuple[str, str]], int]:
    context = _BACKTEST_WORKER_CONTEXT
    samples: list[dict[str, Any]] = []
    cache_hits = 0
    errors: list[tuple[str, str]] = []
    for ticker in tickers:
        try:
            ticker_samples, cache_hit = _backtest_one_ticker_cached(
                ticker,
                context["source"],
                context["benchmark_frame"],
                context["commission"],
                context["stamp_duty"],
                context["slippage"],
                context["split_dates"],
                context["benchmark_signature"],
            )
            samples.extend(ticker_samples)
            cache_hits += int(cache_hit)
        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
            errors.append((ticker, str(exc)))
    return samples, cache_hits, errors, len(tickers)

'''
analytics = replace_once(
    analytics,
    '\ndef run_historical_backtest(\n',
    WORKER_BLOCK + '\ndef run_historical_backtest(\n',
    "analytics process worker helpers",
)

RUN_BLOCK = r'''    unique_tickers = list(dict.fromkeys(tickers))
    samples: list[dict[str, Any]] = []
    total = len(unique_tickers)
    cpu_limit = max(1, (os.cpu_count() or 2) - 1)
    requested_workers = int(workers) if workers is not None else int(BACKTEST_MAX_PROCESSES)
    worker_count = min(
        max(1, requested_workers),
        max(1, int(BACKTEST_MAX_PROCESSES)),
        cpu_limit,
        max(1, total),
    )
    use_process_pool = bool(
        total >= int(BACKTEST_PROCESS_MIN_TICKERS) and worker_count > 1
    )
    engine = "process" if use_process_pool else "sequential"
    benchmark_signature = file_signature(_cache_path(BENCHMARKS[benchmark], source))
    completed = 0
    cache_hits = 0
    next_progress = max(1, int(BACKTEST_PROGRESS_INTERVAL))
    backtest_started = time.perf_counter()

    # Prevent each spawned NumPy/SciPy process from creating its own BLAS thread
    # pool and oversubscribing the CPU.  Spawned Windows workers inherit these.
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

    logger.info(
        "Backtest engine: %s, workers=%d, chunk=%d, persistent cache=%s.",
        engine,
        worker_count,
        int(BACKTEST_CHUNK_SIZE),
        "on" if BACKTEST_CACHE_ENABLED else "off",
    )

    def record_progress(
        batch_samples: list[dict[str, Any]],
        batch_completed: int,
        batch_cache_hits: int,
    ) -> None:
        nonlocal completed, cache_hits, next_progress
        samples.extend(batch_samples)
        completed += int(batch_completed)
        cache_hits += int(batch_cache_hits)
        if completed >= next_progress or completed >= total:
            elapsed = max(time.perf_counter() - backtest_started, 1e-9)
            rate = completed / elapsed
            remaining = max(0, total - completed)
            eta = remaining / rate if rate > 0 else 0.0
            percent = completed / max(total, 1) * 100.0
            logger.info(
                "Backtesting progress: %d/%d tickers, %d samples. %.1f%% | cache=%d | elapsed=%s | ETA=%s | rate=%.2f ticker/s",
                completed,
                total,
                len(samples),
                percent,
                cache_hits,
                _format_duration(elapsed),
                _format_duration(eta),
                rate,
            )
            interval = max(1, int(BACKTEST_PROGRESS_INTERVAL))
            next_progress = ((completed // interval) + 1) * interval

    if use_process_pool:
        chunk_size = max(1, int(BACKTEST_CHUNK_SIZE))
        chunks = [
            unique_tickers[start : start + chunk_size]
            for start in range(0, total, chunk_size)
        ]
        with ProcessPoolExecutor(
            max_workers=worker_count,
            initializer=_init_backtest_worker,
            initargs=(
                source,
                benchmark,
                commission,
                stamp_duty,
                slippage,
                (validation_end, test_start),
                benchmark_signature,
            ),
        ) as executor:
            futures = {
                executor.submit(_backtest_chunk_worker, chunk): chunk for chunk in chunks
            }
            for future in as_completed(futures):
                chunk = futures[future]
                try:
                    batch_samples, batch_hits, errors, batch_count = future.result()
                except Exception as exc:
                    logger.exception("Backtest worker chunk failed: %s", exc)
                    record_progress([], len(chunk), 0)
                    continue
                for ticker, error in errors:
                    logger.warning("Backtest failed for %s: %s", ticker, error)
                record_progress(batch_samples, batch_count, batch_hits)
    else:
        for ticker in unique_tickers:
            try:
                ticker_samples, cache_hit = _backtest_one_ticker_cached(
                    ticker,
                    source,
                    benchmark_frame,
                    commission,
                    stamp_duty,
                    slippage,
                    (validation_end, test_start),
                    benchmark_signature,
                )
            except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
                logger.warning("Backtest failed for %s: %s", ticker, exc)
                ticker_samples, cache_hit = [], False
            record_progress(ticker_samples, 1, int(cache_hit))

'''
analytics = regex_once(
    analytics,
    r'    samples: list\[dict\[str, Any\]\] = \[\]\n    total = len\(tickers\)\n.*?(?=    split_dates = \{)',
    RUN_BLOCK,
    "analytics run_historical_backtest executor",
)
analytics = replace_once(
    analytics,
    '    if not samples:\n        summary.insufficient_test_data = True\n',
    '    summary.cache_hits = int(cache_hits)\n'
    '    summary.elapsed_seconds = float(time.perf_counter() - backtest_started)\n'
    '    summary.worker_count = int(worker_count)\n'
    '    summary.engine = engine\n'
    '    if not samples:\n'
    '        summary.insufficient_test_data = True\n',
    "analytics summary performance metadata",
)
write("analytics.py", analytics)


# ---------------------------------------------------------------------------
# scanner.py — persistent indicator cache, shared by scan/report/backtest.
# ---------------------------------------------------------------------------
scanner = read("scanner.py")
scanner = replace_once(
    scanner,
    '    SCAN_THREADS,\n    SCORING_VERSION,',
    '    SCAN_THREADS,\n    SCORING_VERSION,\n    INDICATOR_CACHE_ENABLED,',
    "scanner indicator cache config import",
)
scanner = replace_once(
    scanner,
    'from indicators import compute_all_indicators\n',
    'from indicators import compute_all_indicators\nfrom performance_cache import load_or_compute_indicators\n',
    "scanner performance cache import",
)
scanner = replace_once(
    scanner,
    '                    downloaded_frames[ticker],\n                )',
    '                    downloaded_frames[ticker],\n                    data_source,\n                )',
    "scanner submit data source",
)
OLD_ANALYSE = '''def _analyse_one_ticker_from_df(\n    ticker_info: TickerInfo,\n    df: pd.DataFrame | None,\n) -> tuple[ScanResult, pd.DataFrame | None]:\n    if df is None:\n        return scan_single_from_df(ticker_info, df), None\n    enriched = compute_all_indicators(df.copy())\n'''
NEW_ANALYSE = '''def _analyse_one_ticker_from_df(\n    ticker_info: TickerInfo,\n    df: pd.DataFrame | None,\n    data_source: str = "tickflow",\n) -> tuple[ScanResult, pd.DataFrame | None]:\n    if df is None:\n        return scan_single_from_df(ticker_info, df), None\n    raw_path = _cache_path(_normalize_ticker(ticker_info.ticker), data_source)\n    enriched, _indicator_cache_hit = load_or_compute_indicators(\n        ticker_info.ticker,\n        df,\n        compute_all_indicators,\n        source_path=raw_path if raw_path.exists() else None,\n        enabled=INDICATOR_CACHE_ENABLED,\n    )\n'''
scanner = replace_once(scanner, OLD_ANALYSE, NEW_ANALYSE, "scanner analyse cache")
scanner = replace_once(
    scanner,
    '    return scan_single_from_df(ticker_info, df)\n\n\ndef run_parallel_indicator_scan(',
    '    return _analyse_one_ticker_from_df(ticker_info, df, data_source)[0]\n\n\ndef run_parallel_indicator_scan(',
    "scanner report cache reuse",
)
write("scanner.py", scanner)


# ---------------------------------------------------------------------------
# downloader.py — parallel Parquet reads + one market-cache manifest per batch.
# ---------------------------------------------------------------------------
downloader = read("downloader.py")
downloader = replace_once(
    downloader,
    'import tempfile\nfrom dataclasses import dataclass',
    'import tempfile\nfrom concurrent.futures import ThreadPoolExecutor, as_completed\nfrom dataclasses import dataclass',
    "downloader cache read executor import",
)
downloader = replace_once(
    downloader,
    '    CACHE_DIR,\n    EXCLUDED_SECURITY_KEYWORDS,',
    '    CACHE_DIR,\n    CACHE_READ_THREADS,\n    EXCLUDED_SECURITY_KEYWORDS,',
    "downloader cache threads config",
)
downloader = replace_once(
    downloader,
    '_UNIVERSE_CACHE_PATH = CACHE_DIR / "_tickflow_universe.json"\n',
    '_UNIVERSE_CACHE_PATH = CACHE_DIR / "_tickflow_universe.json"\n'
    '_MARKET_MANIFEST_PATH = _PRICE_CACHE_DIR / "_manifest.json"\n'
    '_MARKET_MANIFEST_DIRTY: dict[str, dict[str, Any]] = {}\n',
    "downloader manifest constants",
)
MANIFEST_HELPERS = r'''

def _record_market_manifest(ticker: str, df: pd.DataFrame) -> None:
    path = _cache_path(ticker)
    try:
        stat = path.stat()
    except OSError:
        return
    index = pd.DatetimeIndex(pd.to_datetime(df.index, errors="coerce")).dropna()
    latest = pd.Timestamp(index.max()).strftime("%Y-%m-%d") if len(index) else ""
    _MARKET_MANIFEST_DIRTY[normalize_ticker(ticker)] = {
        "path": path.name,
        "rows": int(len(df)),
        "last_date": latest,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "adjust": TICKFLOW_ADJUST,
        "schema": _PRICE_CACHE_SCHEMA_VERSION,
    }


def _flush_market_manifest() -> None:
    if not _MARKET_MANIFEST_DIRTY:
        return
    payload: dict[str, Any] = {}
    try:
        if _MARKET_MANIFEST_PATH.exists():
            loaded = json.loads(_MARKET_MANIFEST_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    payload.update(_MARKET_MANIFEST_DIRTY)
    _MARKET_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = _MARKET_MANIFEST_PATH.with_name(f".{_MARKET_MANIFEST_PATH.name}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(_MARKET_MANIFEST_PATH)
        _MARKET_MANIFEST_DIRTY.clear()
    except OSError:
        logger.debug("Unable to flush market cache manifest", exc_info=True)
    finally:
        temporary.unlink(missing_ok=True)


def _load_caches_parallel(symbols: list[str]) -> dict[str, pd.DataFrame]:
    if not symbols:
        return {}
    workers = min(max(1, int(CACHE_READ_THREADS)), len(symbols))
    if workers <= 1 or len(symbols) < 32:
        return {
            symbol: frame
            for symbol in symbols
            if (frame := _load_cache(symbol)) is not None
        }
    frames: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_load_cache, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                frame = future.result()
            except (OSError, ValueError, TypeError, ImportError):
                frame = None
            if frame is not None:
                frames[symbol] = frame
    return frames
'''
downloader = replace_once(
    downloader,
    '\ndef _latest_completed_trading_day(',
    MANIFEST_HELPERS + '\n\ndef _latest_completed_trading_day(',
    "downloader manifest helpers",
)
OLD_CACHE_LOOP = '''    for symbol in symbols:\n        cached = None if force else _load_cache(symbol)\n        if cached is None:\n            missing.append(symbol)\n        elif cache_first or _cache_has_completed_daily_bar(cached):\n            results[symbol] = cached\n        else:\n            stale_cache[symbol] = cached\n'''
NEW_CACHE_LOOP = '''    cached_frames = {} if force else _load_caches_parallel(symbols)\n    for symbol in symbols:\n        cached = cached_frames.get(symbol)\n        if cached is None:\n            missing.append(symbol)\n        elif cache_first or _cache_has_completed_daily_bar(cached):\n            results[symbol] = cached\n        else:\n            stale_cache[symbol] = cached\n'''
downloader = replace_once(downloader, OLD_CACHE_LOOP, NEW_CACHE_LOOP, "downloader parallel cache loop")
downloader = replace_once(
    downloader,
    '    _log_download_progress(total, total, len(results), failed)\n    logger.info(',
    '    for symbol, frame in results.items():\n'
    '        _record_market_manifest(symbol, frame)\n'
    '    _flush_market_manifest()\n'
    '    _log_download_progress(total, total, len(results), failed)\n'
    '    logger.info(',
    "downloader flush manifest",
)
write("downloader.py", downloader)


# ---------------------------------------------------------------------------
# gui_core.py — backtest determinate progress + ETA in status bar/log coalescing.
# ---------------------------------------------------------------------------
gui = read("gui_core.py")
gui = replace_once(
    gui,
    'ANALYSE_PROGRESS_RE = re.compile(\n    r"ANALYSE progress: (\\d+)/(\\d+) \\((\\d+) successful, (\\d+) failed\\)\\."\n)\n',
    'ANALYSE_PROGRESS_RE = re.compile(\n'
    '    r"ANALYSE progress: (\\d+)/(\\d+) \\((\\d+) successful, (\\d+) failed\\)\\."\n'
    ')\n'
    'BACKTEST_PROGRESS_RE = re.compile(\n'
    '    r"Backtesting progress: (\\d+)/(\\d+) tickers, (\\d+) samples\\."\n'
    ')\n'
    'BACKTEST_ETA_RE = re.compile(r"ETA=([^|]+)")\n',
    "gui backtest regex",
)
gui = replace_once(
    gui,
    '            latest_analyse_progress = None\n            rendered_lines: list[str] = []',
    '            latest_analyse_progress = None\n'
    '            latest_backtest_progress = None\n'
    '            rendered_lines: list[str] = []',
    "gui backtest log coalesce variable",
)
gui = replace_once(
    gui,
    '                elif ANALYSE_PROGRESS_RE.search(line):\n                    latest_analyse_progress = line\n                else:',
    '                elif ANALYSE_PROGRESS_RE.search(line):\n'
    '                    latest_analyse_progress = line\n'
    '                elif BACKTEST_PROGRESS_RE.search(line):\n'
    '                    latest_backtest_progress = line\n'
    '                else:',
    "gui backtest log coalesce match",
)
gui = replace_once(
    gui,
    '            if latest_analyse_progress:\n                rendered_lines.append(latest_analyse_progress)\n            self.append_log',
    '            if latest_analyse_progress:\n'
    '                rendered_lines.append(latest_analyse_progress)\n'
    '            if latest_backtest_progress:\n'
    '                rendered_lines.append(latest_backtest_progress)\n'
    '            self.append_log',
    "gui append latest backtest progress",
)
gui = replace_once(
    gui,
    '        analyse_progress = ANALYSE_PROGRESS_RE.search(text)\n        if fundamental_progress:',
    '        analyse_progress = ANALYSE_PROGRESS_RE.search(text)\n'
    '        backtest_progress = BACKTEST_PROGRESS_RE.search(text)\n'
    '        if fundamental_progress:',
    "gui parse backtest progress",
)
gui = replace_once(
    gui,
    '        elif "Phase 2/2:" in text:\n            self.progress.configure(mode="indeterminate")',
    '        elif backtest_progress:\n'
    '            completed, total, samples = (int(value) for value in backtest_progress.groups())\n'
    '            eta_match = BACKTEST_ETA_RE.search(text)\n'
    '            eta = eta_match.group(1).strip() if eta_match else "计算中"\n'
    '            self.progress.stop()\n'
    '            self.progress.configure(mode="determinate", maximum=max(total, 1), value=completed)\n'
    '            self.status.set(f"历史回测 {completed}/{total} · 样本 {samples} · ETA {eta}")\n'
    '        elif "Phase 2/2:" in text:\n'
    '            self.progress.configure(mode="indeterminate")',
    "gui backtest determinate status",
)
write("gui_core.py", gui)


# ---------------------------------------------------------------------------
# README + regression tests
# ---------------------------------------------------------------------------
readme = read("README.md")
PERF_DOC = r'''

## 性能架构

- **行情层**：TickFlow Free 批量日 K + schema 隔离 Parquet 缓存；缓存读取使用受控并行，并生成 `_manifest.json` 记录每个标的的日期、行数和文件版本。
- **基本面层**：AkShare 只做低频基本面缓存，不参与日常行情扫描热路径。
- **指标层**：原始行情文件未变化且 `SCORING_VERSION` 相同时，复用持久化指标缓存；TickFlow 更新或复权重建会自动失效。
- **回测层**：大股票池自动切换 `ProcessPoolExecutor` 多进程，小任务保持顺序执行以降低启动成本；每个历史候选点只评分一次，并把评分窗口限制在当前模型实际需要的 504 根已计算指标数据。
- **回测缓存**：按行情文件、基准、成本、时间切分、评分版本和回测参数生成哈希。参数与数据没有变化时直接复用单标的历史样本。
- **GUI**：回测每约 25 个标的输出完成数、样本数、缓存命中、耗时、速度和 ETA，进度条同步更新。

第一次全量回测仍需完成真实历史计算；从第二次开始，只要行情/评分参数没有变化，大量标的会直接命中回测缓存。若修改评分逻辑，请同步提升 `SCORING_VERSION`，派生缓存会自动重建。
'''
if "## 性能架构" not in readme:
    readme = readme.rstrip() + PERF_DOC + "\n"
write("README.md", readme)

TESTS = r'''from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import analytics
import gui_core
import performance_cache
from config import (
    BACKTEST_CHUNK_SIZE,
    BACKTEST_MAX_PROCESSES,
    BACKTEST_PROGRESS_INTERVAL,
    BACKTEST_SCORE_WINDOW_BARS,
)


class TestPerformanceConfiguration(unittest.TestCase):
    def test_backtest_limits_are_bounded(self):
        self.assertGreaterEqual(BACKTEST_SCORE_WINDOW_BARS, 504)
        self.assertGreaterEqual(BACKTEST_MAX_PROCESSES, 2)
        self.assertGreaterEqual(BACKTEST_CHUNK_SIZE, 1)
        self.assertLessEqual(BACKTEST_PROGRESS_INTERVAL, 50)

    def test_gui_understands_backtest_progress(self):
        line = (
            "Backtesting progress: 250/5981 tickers, 422 samples. "
            "4.2% | cache=10 | elapsed=3m10s | ETA=1h02m | rate=1.30 ticker/s"
        )
        match = gui_core.BACKTEST_PROGRESS_RE.search(line)
        self.assertIsNotNone(match)
        self.assertEqual(match.groups(), ("250", "5981", "422"))
        self.assertEqual(gui_core.BACKTEST_ETA_RE.search(line).group(1).strip(), "1h02m")


class TestPersistentPerformanceCache(unittest.TestCase):
    def test_backtest_key_changes_with_market_signature(self):
        one = performance_cache.backtest_cache_key({"price_signature": "a", "cost": 1})
        two = performance_cache.backtest_cache_key({"price_signature": "b", "cost": 1})
        self.assertNotEqual(one, two)
        self.assertEqual(
            one,
            performance_cache.backtest_cache_key({"price_signature": "a", "cost": 1}),
        )

    def test_indicator_cache_reuses_same_source_file(self):
        frame = pd.DataFrame(
            {
                "Open": [1.0, 2.0],
                "High": [2.0, 3.0],
                "Low": [0.5, 1.5],
                "Close": [1.5, 2.5],
                "Volume": [100.0, 200.0],
            },
            index=pd.to_datetime(["2026-08-06", "2026-08-07"]),
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "raw.parquet"
            source.write_bytes(b"raw")
            indicator_dir = Path(directory) / "indicators"
            with patch.object(performance_cache, "INDICATOR_CACHE_DIR", indicator_dir):
                calls = {"count": 0}

                def compute(value):
                    calls["count"] += 1
                    result = value.copy()
                    result["MA20"] = result["Close"]
                    return result

                first, first_hit = performance_cache.load_or_compute_indicators(
                    "000001.SZ", frame, compute, source_path=source
                )
                second, second_hit = performance_cache.load_or_compute_indicators(
                    "000001.SZ", frame, compute, source_path=source
                )
                self.assertFalse(first_hit)
                self.assertTrue(second_hit)
                self.assertEqual(calls["count"], 1)
                pd.testing.assert_frame_equal(first, second, check_freq=False)


class TestBacktestHotPath(unittest.TestCase):
    def test_signal_points_is_compatibility_projection(self):
        frame = pd.DataFrame(
            {
                "Close": [10.0] * 40,
                "VolMA20": [2.0] * 40,
                "VolMA120": [1.0] * 40,
                "CMF": [0.1] * 40,
                "MA50": [10.0] * 40,
            }
        )
        points = analytics._signal_points(frame, cooldown=10)
        self.assertEqual(points, analytics._legacy_signal_points(frame, 10))

    def test_small_backtest_does_not_force_process_pool(self):
        benchmark = pd.DataFrame(
            {"Close": [100.0] * 400},
            index=pd.date_range("2025-01-01", periods=400, freq="B"),
        )
        with patch.object(analytics, "_load_benchmark_frames", return_value={"沪深300": benchmark}), patch.object(
            analytics, "_backtest_one_ticker_cached", return_value=([], False)
        ) as worker, patch.object(analytics, "BACKTEST_PROCESS_MIN_TICKERS", 100):
            summary = analytics.run_historical_backtest(["000001.SZ"], workers=8)
        worker.assert_called_once()
        self.assertEqual(summary.engine, "sequential")


if __name__ == "__main__":
    unittest.main()
'''
write("test_performance_regressions.py", TESTS)

print("Performance optimization patch applied.")
