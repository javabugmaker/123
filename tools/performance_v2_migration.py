from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"missing replacement target: {label}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, replacement: str, label: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        raise RuntimeError(f"missing start marker: {label}")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise RuntimeError(f"missing end marker: {label}")
    return text[:start_index] + replacement + text[end_index:]


# ---------------------------------------------------------------------------
# config.py
# ---------------------------------------------------------------------------
config = read("config.py")
config = replace_once(
    config,
    'SCORING_VERSION: str = "2026-08-08-v13-performance-cache-process-backtest"',
    'SCORING_VERSION: str = "2026-08-08-v14-fast-exact-incremental-backtest"',
    "scoring version",
)
config = replace_once(
    config,
    '''# Performance: score functions need at most 504 historical rows once indicators
# have been precomputed.  Keeping this bound avoids repeatedly scanning 10 years.
BACKTEST_SCORE_WINDOW_BARS: Final[int] = 504
BACKTEST_MAX_PROCESSES: Final[int] = 8
BACKTEST_PROCESS_MIN_TICKERS: Final[int] = 100
BACKTEST_CHUNK_SIZE: Final[int] = 8
BACKTEST_PROGRESS_INTERVAL: Final[int] = 25
BACKTEST_CACHE_ENABLED: Final[bool] = True
INDICATOR_CACHE_ENABLED: Final[bool] = True
CACHE_READ_THREADS: Final[int] = 8
''',
    '''# Exact backtests preserve the complete historical scoring semantics.  Fast
# full-market calibration uses a narrower window and sparser signal sampling;
# final Top50 research automatically stays on Exact mode.
BACKTEST_SCORE_WINDOW_BARS: Final[int] = 504
BACKTEST_FAST_SCORE_WINDOW_BARS: Final[int] = 252
BACKTEST_FAST_COOLDOWN_DAYS: Final[int] = 40
BACKTEST_FAST_CANDIDATE_GAP_DAYS: Final[int] = 5
BACKTEST_AUTO_EXACT_MAX_TICKERS: Final[int] = 100
BACKTEST_MAX_PROCESSES: Final[int] = 12
BACKTEST_PROCESS_MIN_TICKERS: Final[int] = 8
BACKTEST_CHUNK_SIZE: Final[int] = 4
BACKTEST_FAST_CHUNK_SIZE: Final[int] = 12
BACKTEST_PROGRESS_INTERVAL: Final[int] = 25
BACKTEST_INCREMENTAL_TAIL_BARS: Final[int] = 900
INDICATOR_INCREMENTAL_LOOKBACK_BARS: Final[int] = 620
BACKTEST_CACHE_ENABLED: Final[bool] = True
INDICATOR_CACHE_ENABLED: Final[bool] = True
CACHE_READ_THREADS: Final[int] = 8
''',
    "performance config",
)
write("config.py", config)


# ---------------------------------------------------------------------------
# performance_cache.py -- incremental indicator/backtest state cache
# ---------------------------------------------------------------------------
performance_cache = r'''from __future__ import annotations

"""Persistent compute caches used by scanner and historical backtests.

The cache layer distinguishes immutable strategy parameters from market state.
Appending a new TickFlow daily bar therefore reuses the existing indicator and
backtest history instead of invalidating ten years of derived work.  Historical
price changes (for example a forward-adjustment rebase) are detected by a tail
fingerprint and still force a safe full rebuild.
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

from config import CACHE_DIR, INDICATOR_INCREMENTAL_LOOKBACK_BARS, SCORING_VERSION

INDICATOR_CACHE_VERSION = "v3"
BACKTEST_CACHE_VERSION = "v5"
INDICATOR_CACHE_DIR = CACHE_DIR / f"_indicators_{INDICATOR_CACHE_VERSION}"
BACKTEST_CACHE_DIR = CACHE_DIR / f"_backtest_{BACKTEST_CACHE_VERSION}"
_MARKET_COLUMNS = ("Open", "High", "Low", "Close", "Volume", "Amount")
_CUMULATIVE_COLUMNS = ("OBV", "AD")


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


def _normalized_index(df: pd.DataFrame) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(df.index, errors="coerce"))


def _frame_identity(df: pd.DataFrame) -> dict[str, Any]:
    if df is None or df.empty:
        return {"rows": 0, "first": "", "last": ""}
    index = _normalized_index(df).dropna()
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


def market_tail_fingerprint(
    df: pd.DataFrame,
    *,
    end_date: str | pd.Timestamp | None = None,
    rows: int = 12,
) -> str:
    """Hash a small OHLCV tail ending at ``end_date`` for append safety."""
    if df is None or df.empty:
        return ""
    frame = df.copy(deep=False)
    frame.index = _normalized_index(frame)
    frame = frame[~frame.index.isna()].sort_index()
    if end_date is not None:
        cutoff = pd.Timestamp(end_date)
        frame = frame.loc[frame.index <= cutoff]
    columns = [column for column in _MARKET_COLUMNS if column in frame.columns]
    if frame.empty or not columns:
        return ""
    tail = frame[columns].tail(max(1, int(rows))).apply(pd.to_numeric, errors="coerce")
    payload = {
        "dates": [value.isoformat() for value in pd.DatetimeIndex(tail.index)],
        "columns": columns,
        "values": [
            [None if not np.isfinite(value) else round(float(value), 8) for value in row]
            for row in tail.to_numpy(dtype=float)
        ],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def market_cache_state(df: pd.DataFrame) -> dict[str, Any]:
    identity = _frame_identity(df)
    return {
        "rows": int(identity.get("rows", 0)),
        "first": str(identity.get("first", "")),
        "last": str(identity.get("last", "")),
        "tail_fingerprint": market_tail_fingerprint(df),
    }


def market_prefix_matches(df: pd.DataFrame, state: dict[str, Any] | None) -> bool:
    """Return True when current market data safely extends a cached prefix."""
    if df is None or df.empty or not state:
        return False
    last_text = str(state.get("last", "")).strip()
    expected = str(state.get("tail_fingerprint", "")).strip()
    if not last_text or not expected:
        return False
    try:
        last_date = pd.Timestamp(last_text)
    except (TypeError, ValueError):
        return False
    index = _normalized_index(df)
    if last_date not in index:
        return False
    current = market_tail_fingerprint(df, end_date=last_date)
    return bool(current and current == expected)


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


def _read_indicator_cache(data_path: Path) -> pd.DataFrame | None:
    try:
        cached = pd.read_parquet(data_path)
        cached.index = pd.to_datetime(cached.index, errors="coerce")
        cached = cached[~cached.index.isna()].sort_index()
        return cached if not cached.empty else None
    except (OSError, ValueError, TypeError, ImportError):
        return None


def _align_cumulative_tail(cached: pd.DataFrame, tail: pd.DataFrame, anchor: pd.Timestamp) -> None:
    """Align cumulative indicators (OBV/A-D) to the cached historical level."""
    if anchor not in cached.index or anchor not in tail.index:
        return
    for column in _CUMULATIVE_COLUMNS:
        if column not in cached.columns or column not in tail.columns:
            continue
        old_value = pd.to_numeric(pd.Series([cached.at[anchor, column]]), errors="coerce").iloc[0]
        new_value = pd.to_numeric(pd.Series([tail.at[anchor, column]]), errors="coerce").iloc[0]
        if pd.notna(old_value) and pd.notna(new_value):
            tail[column] = pd.to_numeric(tail[column], errors="coerce") + float(old_value - new_value)


def load_or_compute_indicators(
    ticker: str,
    frame: pd.DataFrame,
    compute_fn: Callable[[pd.DataFrame], pd.DataFrame],
    *,
    source_path: Path | None = None,
    enabled: bool = True,
) -> tuple[pd.DataFrame, bool]:
    """Return indicators, incrementally extending a valid persistent cache."""
    if frame is None or frame.empty:
        return frame, False
    if not enabled or source_path is None or not source_path.exists():
        return compute_fn(frame.copy()), False

    source = frame.copy(deep=False)
    source.index = _normalized_index(source)
    source = source[~source.index.isna()].sort_index()
    signature = data_signature(source, source_path)
    stem = _safe_stem(ticker)
    data_path = INDICATOR_CACHE_DIR / f"{stem}.parquet"
    meta_path = INDICATOR_CACHE_DIR / f"{stem}.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        meta = {}

    cached = None
    if (
        data_path.exists()
        and meta.get("version") == INDICATOR_CACHE_VERSION
        and meta.get("scoring_version") == SCORING_VERSION
    ):
        cached = _read_indicator_cache(data_path)

    if cached is not None:
        source_last = pd.Timestamp(source.index.max())
        cache_last = pd.Timestamp(cached.index.max())
        if (
            len(cached) == len(source)
            and source_last == cache_last
            and market_tail_fingerprint(source) == market_tail_fingerprint(cached)
        ):
            if meta.get("signature") != signature:
                try:
                    _atomic_json(
                        meta_path,
                        {
                            "version": INDICATOR_CACHE_VERSION,
                            "scoring_version": SCORING_VERSION,
                            "signature": signature,
                            **market_cache_state(source),
                        },
                    )
                except (OSError, ValueError, TypeError):
                    pass
            return cached, True

        cached_state = {
            "rows": len(cached),
            "first": str(cached.index.min()),
            "last": str(cache_last),
            "tail_fingerprint": market_tail_fingerprint(cached),
        }
        if source_last > cache_last and market_prefix_matches(source, cached_state):
            cache_last_pos = int(source.index.get_indexer([cache_last])[0])
            start = max(0, cache_last_pos - max(252, int(INDICATOR_INCREMENTAL_LOOKBACK_BARS)))
            tail_raw = source.iloc[start:].copy()
            tail = compute_fn(tail_raw)
            tail.index = pd.to_datetime(tail.index, errors="coerce")
            _align_cumulative_tail(cached, tail, cache_last)
            appended = tail.loc[tail.index > cache_last]
            if not appended.empty:
                merged = pd.concat([cached, appended], axis=0)
                merged = merged[~merged.index.duplicated(keep="last")].sort_index()
                if len(merged) == len(source) and pd.Timestamp(merged.index.max()) == source_last:
                    try:
                        _atomic_parquet(data_path, merged)
                        _atomic_json(
                            meta_path,
                            {
                                "version": INDICATOR_CACHE_VERSION,
                                "scoring_version": SCORING_VERSION,
                                "signature": signature,
                                **market_cache_state(source),
                            },
                        )
                    except (OSError, ValueError, TypeError, ImportError):
                        pass
                    return merged, True

    enriched = compute_fn(source.copy())
    try:
        _atomic_parquet(data_path, enriched)
        _atomic_json(
            meta_path,
            {
                "version": INDICATOR_CACHE_VERSION,
                "scoring_version": SCORING_VERSION,
                "signature": signature,
                **market_cache_state(source),
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


def _backtest_path(ticker: str) -> Path:
    return BACKTEST_CACHE_DIR / f"{_safe_stem(ticker)}.json.gz"


def load_backtest_cache_state(ticker: str, key: str) -> dict[str, Any] | None:
    path = _backtest_path(ticker)
    if not key or not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("key") != key or not isinstance(payload.get("samples"), list):
            return None
        return payload
    except (OSError, EOFError, TypeError, ValueError, json.JSONDecodeError):
        return None


def load_backtest_cache(ticker: str, key: str) -> list[dict[str, Any]] | None:
    payload = load_backtest_cache_state(ticker, key)
    return list(payload["samples"]) if payload is not None else None


def _json_default(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value)!r}")


def save_backtest_cache(
    ticker: str,
    key: str,
    samples: list[dict[str, Any]],
    *,
    state: dict[str, Any] | None = None,
) -> None:
    if not key:
        return
    BACKTEST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _backtest_path(ticker)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".json.gz", delete=False) as handle:
        temporary = Path(handle.name)
    payload: dict[str, Any] = {"key": key, "samples": samples}
    if state:
        payload["state"] = state
    try:
        with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=3) as stream:
            json.dump(payload, stream, ensure_ascii=False, default=_json_default, separators=(",", ":"))
        temporary.replace(path)
    except (OSError, TypeError, ValueError):
        temporary.unlink(missing_ok=True)
        return
    finally:
        temporary.unlink(missing_ok=True)
'''
write("performance_cache.py", performance_cache)


# ---------------------------------------------------------------------------
# analytics.py
# ---------------------------------------------------------------------------
analytics = read("analytics.py")
analytics = replace_once(
    analytics,
    '''    BACKTEST_SIGNAL_COOLDOWN_DAYS,
    BACKTEST_SCORE_WINDOW_BARS,
    BACKTEST_MAX_PROCESSES,
    BACKTEST_PROCESS_MIN_TICKERS,
    BACKTEST_CHUNK_SIZE,
    BACKTEST_PROGRESS_INTERVAL,
''',
    '''    BACKTEST_SIGNAL_COOLDOWN_DAYS,
    BACKTEST_SCORE_WINDOW_BARS,
    BACKTEST_FAST_SCORE_WINDOW_BARS,
    BACKTEST_FAST_COOLDOWN_DAYS,
    BACKTEST_FAST_CANDIDATE_GAP_DAYS,
    BACKTEST_AUTO_EXACT_MAX_TICKERS,
    BACKTEST_MAX_PROCESSES,
    BACKTEST_PROCESS_MIN_TICKERS,
    BACKTEST_CHUNK_SIZE,
    BACKTEST_FAST_CHUNK_SIZE,
    BACKTEST_PROGRESS_INTERVAL,
    BACKTEST_INCREMENTAL_TAIL_BARS,
''',
    "analytics config imports",
)
analytics = replace_once(
    analytics,
    '''from performance_cache import (
    backtest_cache_key,
    file_signature,
    load_backtest_cache,
    load_or_compute_indicators,
    save_backtest_cache,
)
from score import entry_point, score_ticker
''',
    '''from performance_cache import (
    backtest_cache_key,
    load_backtest_cache_state,
    load_or_compute_indicators,
    market_cache_state,
    market_prefix_matches,
    save_backtest_cache,
)
from score import breakout_score, entry_point, score_ticker, value_trap_risk
''',
    "analytics cache imports",
)
analytics = replace_once(
    analytics,
    '''    worker_count: int = 0
    engine: str = "sequential"
    objective: str = "net_excess_return_20d"
''',
    '''    worker_count: int = 0
    engine: str = "sequential"
    mode: str = "auto"
    objective: str = "net_excess_return_20d"
''',
    "summary mode",
)

new_hot_path = r'''@dataclass(frozen=True)
class BacktestExecutionProfile:
    name: str
    cooldown: int
    score_window: int
    historical_volume_profile: bool
    candidate_gap: int
    fast_prefilter: bool
    chunk_size: int


def _resolve_backtest_profile(mode: str | None, ticker_count: int) -> BacktestExecutionProfile:
    normalized = str(mode or "auto").strip().lower()
    if normalized not in {"auto", "fast", "exact"}:
        raise ValueError(f"unsupported backtest mode: {mode}")
    if normalized == "auto":
        normalized = (
            "exact"
            if int(ticker_count) <= int(BACKTEST_AUTO_EXACT_MAX_TICKERS)
            else "fast"
        )
    if normalized == "exact":
        return BacktestExecutionProfile(
            name="exact",
            cooldown=max(1, int(BACKTEST_SIGNAL_COOLDOWN_DAYS)),
            score_window=max(252, int(BACKTEST_SCORE_WINDOW_BARS)),
            historical_volume_profile=bool(ENABLE_VOLUME_PROFILE),
            candidate_gap=1,
            fast_prefilter=False,
            chunk_size=max(1, int(BACKTEST_CHUNK_SIZE)),
        )
    return BacktestExecutionProfile(
        name="fast",
        cooldown=max(1, int(BACKTEST_FAST_COOLDOWN_DAYS)),
        score_window=max(252, int(BACKTEST_FAST_SCORE_WINDOW_BARS)),
        historical_volume_profile=False,
        candidate_gap=max(1, int(BACKTEST_FAST_CANDIDATE_GAP_DAYS)),
        fast_prefilter=True,
        chunk_size=max(1, int(BACKTEST_FAST_CHUNK_SIZE)),
    )


def _backtest_scoring_window(
    enriched: pd.DataFrame,
    index: int,
    *,
    score_window: int | None = None,
    include_volume_profile: bool | None = None,
) -> pd.DataFrame:
    """Return the bounded, point-in-time frame consumed by score_ticker."""
    end = int(index) + 1
    window = int(score_window or BACKTEST_SCORE_WINDOW_BARS)
    start = max(0, end - max(252, window))
    historical = enriched.iloc[start:end].copy(deep=False)
    vp_columns = [
        "VP_HVN_Center",
        "DistToHVN_Pct",
        "Above_HVN",
        "VP_LVN_Center",
        "DistToLVN_Pct",
    ]
    historical = historical.drop(columns=vp_columns, errors="ignore")
    should_compute_vp = ENABLE_VOLUME_PROFILE if include_volume_profile is None else bool(include_volume_profile)
    if should_compute_vp:
        historical = historical.copy(deep=False)
        try:
            compute_volume_profile(historical)
        except (ArithmeticError, TypeError, ValueError):
            logger.debug("Historical volume profile failed.", exc_info=True)
    return historical


def _candidate_endpoint_matrix(
    enriched: pd.DataFrame,
    *,
    fast_prefilter: bool,
) -> tuple[np.ndarray, np.ndarray]:
    """Vectorize cheap endpoint features before any historical score_ticker call."""
    index = enriched.index
    def numeric(name: str) -> pd.Series:
        if name not in enriched.columns:
            return pd.Series(np.nan, index=index, dtype=float)
        return pd.to_numeric(enriched[name], errors="coerce")

    close = numeric("Close")
    high = numeric("High")
    low = numeric("Low")
    ma20 = numeric("MA20")
    ma50 = numeric("MA50")
    atr = numeric("ATR14")
    support = low.rolling(20, min_periods=20).min()
    resistance = high.shift(1).rolling(20, min_periods=20).max()
    effective_atr = atr.where(atr.gt(0), close * 0.03)
    near_support = close.le(support + effective_atr * 1.5)
    five_day_up = close.ge(close.shift(5))
    trend_candidate = close.gt(ma20) & (ma20.ge(ma50) | five_day_up | near_support)
    breakout_flag = close.gt(resistance).fillna(False)
    broad = (trend_candidate | near_support | breakout_flag).fillna(False)

    if fast_prefilter:
        volume = numeric("Volume")
        vol20 = numeric("VolMA20")
        volume_ratio = volume / vol20.replace(0, np.nan)
        cmf = numeric("CMF")
        ad_slope = numeric("AD_Slope")
        flow_ok = cmf.ge(-0.02) | ad_slope.gt(0)
        volume_ok = volume_ratio.ge(0.85)
        evidence_available = volume_ratio.notna() | cmf.notna() | ad_slope.notna()
        support_ready = near_support & (flow_ok | volume_ok)
        trend_ready = close.gt(ma20) & (ma20.ge(ma50) | five_day_up) & (flow_ok | volume_ok)
        filtered = (breakout_flag | support_ready | trend_ready).fillna(False)
        broad = broad.where(~evidence_available, filtered)

    candidates = np.flatnonzero(broad.to_numpy(dtype=bool))
    return candidates, breakout_flag.to_numpy(dtype=bool)


def _signal_evaluations(
    enriched: pd.DataFrame,
    cooldown: int = BACKTEST_SIGNAL_COOLDOWN_DAYS,
    is_etf: bool = False,
    *,
    profile: BacktestExecutionProfile | None = None,
    start_index: int | None = None,
) -> list[tuple[int, float, str]]:
    """Find actionable historical entries with lazy exact scoring.

    Cheap endpoint logic first determines whether a date can possibly become an
    actionable entry.  Full score_ticker and historical Volume Profile are only
    executed for dates that survive that gate; Exact mode therefore preserves
    the final actionable score while avoiding thousands of wasted full scores.
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

    if profile is None:
        profile = BacktestExecutionProfile(
            name="exact",
            cooldown=max(1, int(cooldown)),
            score_window=max(252, int(BACKTEST_SCORE_WINDOW_BARS)),
            historical_volume_profile=bool(ENABLE_VOLUME_PROFILE),
            candidate_gap=1,
            fast_prefilter=False,
            chunk_size=max(1, int(BACKTEST_CHUNK_SIZE)),
        )
    cooldown = max(1, int(profile.cooldown))
    candidates, breakout_flags = _candidate_endpoint_matrix(
        enriched, fast_prefilter=profile.fast_prefilter
    )
    minimum_index = max(251, int(start_index) if start_index is not None else 251)
    last_signal = minimum_index - cooldown
    last_evaluated = minimum_index - max(1, int(profile.candidate_gap))
    evaluations: list[tuple[int, float, str]] = []

    for index in candidates:
        index = int(index)
        if index < minimum_index:
            continue
        if index >= len(enriched) - BACKTEST_OUTCOME_HORIZON_DAYS:
            continue
        if index - last_signal < cooldown:
            continue
        if (
            profile.candidate_gap > 1
            and index - last_evaluated < profile.candidate_gap
            and not breakout_flags[index]
        ):
            continue
        last_evaluated = index

        historical = _backtest_scoring_window(
            enriched,
            index,
            score_window=profile.score_window,
            include_volume_profile=False,
        )
        quick_breakout = breakout_score(historical)
        quick_trap = value_trap_risk(historical)
        quick_entry = entry_point(
            historical,
            breakout=quick_breakout,
            volume_score=None,
            value_trap_risk_value=quick_trap,
        )
        quick_signal = str(quick_entry.get("signal", "AVOID")).upper()
        # volume_score can only change the price-breakout branch into
        # BREAKOUT_CONFIRM.  All other non-actionable quick signals are safe to
        # reject without a full historical score.
        if (
            quick_signal not in _BACKTEST_ACTIONABLE_SIGNALS
            and not bool(quick_entry.get("price_breakout", False))
        ):
            continue

        scoring_frame = historical
        if profile.historical_volume_profile:
            scoring_frame = _backtest_scoring_window(
                enriched,
                index,
                score_window=profile.score_window,
                include_volume_profile=True,
            )
        historical_score = score_ticker(scoring_frame, is_etf=is_etf)
        historical_entry = entry_point(
            scoring_frame,
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
        evaluations.append((index, float(final_score), signal))
        last_signal = index
    return evaluations


class _SignalPointList(list[int]):
    """List-compatible signal points carrying precomputed real-run evaluations."""

    def __init__(self, evaluations: list[tuple[int, float, str]]) -> None:
        super().__init__(index for index, _score, _signal in evaluations)
        self.evaluations = evaluations


def _signal_points(
    enriched: pd.DataFrame,
    cooldown: int = BACKTEST_SIGNAL_COOLDOWN_DAYS,
    is_etf: bool = False,
) -> list[int]:
    evaluations = _signal_evaluations(
        enriched, cooldown=cooldown, is_etf=is_etf
    )
    return _SignalPointList(evaluations)


def _historical_entry_signal(
    historical: pd.DataFrame, historical_score: Any
) -> str:
    try:
        entry = entry_point(
            historical,
            breakout=_finite_float(
                getattr(historical_score, "breakout_score", np.nan), np.nan
            ),
            volume_score=_finite_float(
                getattr(historical_score, "volume", np.nan), np.nan
            ),
            value_trap_risk_value=_finite_float(
                getattr(historical_score, "value_trap_risk", np.nan), np.nan
            ),
        )
    except (ArithmeticError, TypeError, ValueError, KeyError, IndexError):
        return "UNKNOWN"
    return str(entry.get("signal", "UNKNOWN")).upper()


def _backtest_one_ticker(
    ticker: str,
    source: str,
    benchmark_frame: pd.DataFrame | None = None,
    commission: float = 0.0003,
    stamp_duty: float = 0.0005,
    slippage: float = 0.001,
    split_dates: tuple[pd.Timestamp | None, pd.Timestamp | None] = (None, None),
    *,
    profile: BacktestExecutionProfile | None = None,
    signal_start_index: int | None = None,
    sample_min_signal_index: int | None = None,
    frame: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    if frame is None:
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
    if profile is None and signal_start_index is None:
        signal_points = _signal_points(enriched, is_etf=is_etf)
    else:
        active_profile = profile or _resolve_backtest_profile("exact", 1)
        signal_points = _SignalPointList(
            _signal_evaluations(
                enriched,
                is_etf=is_etf,
                profile=active_profile,
                start_index=signal_start_index,
            )
        )
    if not signal_points:
        return []

    attached_evaluations = getattr(signal_points, "evaluations", None)
    if attached_evaluations is not None:
        evaluation_map = {
            index: (score, signal)
            for index, score, signal in attached_evaluations
        }
    else:
        evaluation_map: dict[int, tuple[float, str]] = {}
        for index in signal_points:
            historical = _backtest_scoring_window(enriched, int(index))
            historical_score = score_ticker(historical, is_etf=is_etf)
            final_score = _finite_float(
                getattr(historical_score, "final_score", np.nan), np.nan
            )
            if not np.isfinite(final_score):
                final_score = _finite_float(
                    getattr(historical_score, "total", np.nan), 0.0
                )
            evaluation_map[int(index)] = (
                float(final_score),
                _historical_entry_signal(historical, historical_score),
            )

    opens = enriched["Open"].to_numpy(dtype=float) if "Open" in enriched else np.full(len(enriched), np.nan)
    lows = enriched["Low"].to_numpy(dtype=float) if "Low" in enriched else np.full(len(enriched), np.nan)
    closes = enriched["Close"].to_numpy(dtype=float)
    highs = enriched["High"].to_numpy(dtype=float) if "High" in enriched else closes.copy()
    outcome_horizon = max(60, int(BACKTEST_OUTCOME_HORIZON_DAYS))
    minimum_sample_index = int(sample_min_signal_index) if sample_min_signal_index is not None else 0
    valid_points: list[int] = []
    for index in signal_points:
        if int(index) < minimum_sample_index:
            continue
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


def _relabel_sample_splits(
    samples: list[dict[str, Any]],
    split_dates: tuple[pd.Timestamp | None, pd.Timestamp | None],
) -> list[dict[str, Any]]:
    validation_end, test_start = split_dates
    result: list[dict[str, Any]] = []
    for sample in samples:
        item = dict(sample)
        entry_date = pd.Timestamp(item.get("entry_date"))
        if test_start is not None and entry_date >= test_start:
            item["split"] = "test"
        elif validation_end is not None and entry_date >= validation_end:
            item["split"] = "validation"
        else:
            item["split"] = "train"
        result.append(item)
    return result


def _reweight_samples(samples: list[dict[str, Any]], frame: pd.DataFrame) -> list[dict[str, Any]]:
    if not samples:
        return samples
    positions = {
        pd.Timestamp(value).strftime("%Y-%m-%d"): index
        for index, value in enumerate(pd.DatetimeIndex(frame.index))
    }
    horizon = max(60, int(BACKTEST_OUTCOME_HORIZON_DAYS))
    ordered = sorted(samples, key=lambda item: str(item.get("signal_date", "")))
    previous: int | None = None
    for item in ordered:
        position = positions.get(str(item.get("signal_date", "")))
        if position is None:
            continue
        spacing = horizon if previous is None else max(1, position - previous)
        item["sample_weight"] = round(min(1.0, spacing / float(horizon)), 4)
        previous = position
    return ordered


def _merge_backtest_samples(
    historical: list[dict[str, Any]],
    tail: list[dict[str, Any]],
    frame: pd.DataFrame,
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in [*historical, *tail]:
        key = (
            str(item.get("ticker", "")),
            str(item.get("signal_date", "")),
            str(item.get("entry_signal", "")),
        )
        merged[key] = dict(item)
    return _reweight_samples(list(merged.values()), frame)


def _backtest_one_ticker_cached(
    ticker: str,
    source: str,
    benchmark_frame: pd.DataFrame | None,
    commission: float,
    stamp_duty: float,
    slippage: float,
    split_dates: tuple[pd.Timestamp | None, pd.Timestamp | None],
    benchmark_signature: str = "",
    *,
    profile: BacktestExecutionProfile | None = None,
    benchmark_name: str = "沪深300",
) -> tuple[list[dict[str, Any]], bool]:
    del benchmark_signature  # v5 validates benchmark data by market-state prefix instead.
    frame = _load_cache(ticker, source)
    if frame is None or len(frame) < 300:
        return [], False
    active_profile = profile or _resolve_backtest_profile("exact", 1)
    cache_key = backtest_cache_key(
        {
            "ticker": str(ticker),
            "source": str(source),
            "benchmark": str(benchmark_name),
            "commission": float(commission),
            "stamp_duty": float(stamp_duty),
            "slippage": float(slippage),
            "cooldown": int(active_profile.cooldown),
            "horizon": int(BACKTEST_OUTCOME_HORIZON_DAYS),
            "score_window": int(active_profile.score_window),
            "mode": active_profile.name,
            "historical_volume_profile": bool(active_profile.historical_volume_profile),
            "candidate_gap": int(active_profile.candidate_gap),
            "fast_prefilter": bool(active_profile.fast_prefilter),
        }
    )
    current_market = market_cache_state(frame)
    current_benchmark = market_cache_state(benchmark_frame) if benchmark_frame is not None else {}
    cached_payload = load_backtest_cache_state(ticker, cache_key) if BACKTEST_CACHE_ENABLED else None
    if cached_payload is not None:
        cached_samples = list(cached_payload.get("samples", []))
        cached_state = cached_payload.get("state", {}) if isinstance(cached_payload.get("state", {}), dict) else {}
        cached_market = cached_state.get("market", {})
        cached_benchmark = cached_state.get("benchmark", {})
        market_ok = market_prefix_matches(frame, cached_market)
        benchmark_ok = (
            not cached_benchmark
            or benchmark_frame is None
            or market_prefix_matches(benchmark_frame, cached_benchmark)
        )
        if market_ok and benchmark_ok:
            old_rows = int(cached_market.get("rows", 0) or 0)
            old_last = str(cached_market.get("last", ""))
            same_market = old_rows == len(frame) and old_last == str(current_market.get("last", ""))
            if same_market:
                return _relabel_sample_splits(cached_samples, split_dates), True

            cutoff_index = max(251, len(frame) - max(300, int(BACKTEST_INCREMENTAL_TAIL_BARS)))
            warmup = max(
                251,
                cutoff_index
                - max(
                    int(active_profile.cooldown),
                    int(BACKTEST_OUTCOME_HORIZON_DAYS),
                    int(active_profile.candidate_gap),
                ),
            )
            cutoff_date = pd.Timestamp(frame.index[cutoff_index])
            retained = [
                dict(item)
                for item in cached_samples
                if pd.Timestamp(item.get("signal_date")) < cutoff_date
            ]
            tail_samples = _backtest_one_ticker(
                ticker,
                source,
                benchmark_frame,
                commission,
                stamp_duty,
                slippage,
                split_dates,
                profile=active_profile,
                signal_start_index=warmup,
                sample_min_signal_index=cutoff_index,
                frame=frame,
            )
            samples = _merge_backtest_samples(retained, tail_samples, frame)
            samples = _relabel_sample_splits(samples, split_dates)
            if BACKTEST_CACHE_ENABLED:
                save_backtest_cache(
                    ticker,
                    cache_key,
                    samples,
                    state={"market": current_market, "benchmark": current_benchmark},
                )
            return samples, True

    samples = _backtest_one_ticker(
        ticker,
        source,
        benchmark_frame,
        commission,
        stamp_duty,
        slippage,
        split_dates,
        profile=active_profile,
        frame=frame,
    )
    if BACKTEST_CACHE_ENABLED:
        save_backtest_cache(
            ticker,
            cache_key,
            samples,
            state={"market": current_market, "benchmark": current_benchmark},
        )
    return samples, False

'''
analytics = replace_between(
    analytics,
    "def _backtest_scoring_window(",
    "def _backtest_evidence(",
    new_hot_path,
    "analytics hot path",
)

new_runner = r'''_BACKTEST_WORKER_CONTEXT: dict[str, Any] = {}


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _adaptive_worker_count(
    total: int,
    requested: int | None,
    profile: BacktestExecutionProfile,
) -> int:
    cpu_limit = max(1, (os.cpu_count() or 2) - 1)
    hard_limit = min(max(1, int(BACKTEST_MAX_PROCESSES)), cpu_limit, max(1, total))
    if requested is not None:
        return min(hard_limit, max(1, int(requested)))
    utilization = 0.90 if profile.name == "fast" else 0.75
    target = max(2, int(round(cpu_limit * utilization))) if total >= BACKTEST_PROCESS_MIN_TICKERS else 1
    return min(hard_limit, target, max(1, total))


def _init_backtest_worker(
    source: str,
    benchmark: str,
    commission: float,
    stamp_duty: float,
    slippage: float,
    split_dates: tuple[pd.Timestamp | None, pd.Timestamp | None],
    benchmark_signature: str,
    profile: BacktestExecutionProfile,
) -> None:
    global _BACKTEST_WORKER_CONTEXT
    benchmark_frame = _load_cache(BENCHMARKS[benchmark], source)
    _BACKTEST_WORKER_CONTEXT = {
        "source": source,
        "benchmark": benchmark,
        "benchmark_frame": benchmark_frame,
        "commission": commission,
        "stamp_duty": stamp_duty,
        "slippage": slippage,
        "split_dates": split_dates,
        "benchmark_signature": benchmark_signature,
        "profile": profile,
    }


def _backtest_chunk_worker(
    tickers: list[str],
) -> tuple[pd.DataFrame, int, list[tuple[str, str]], int]:
    context = _BACKTEST_WORKER_CONTEXT
    frames: list[pd.DataFrame] = []
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
                profile=context["profile"],
                benchmark_name=context["benchmark"],
            )
            if ticker_samples:
                frames.append(pd.DataFrame.from_records(ticker_samples))
            cache_hits += int(cache_hit)
        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
            errors.append((ticker, str(exc)))
    batch = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return batch, cache_hits, errors, len(tickers)


def run_historical_backtest(
    tickers: list[str],
    source: str = "eastmoney",
    objective: str = "net_excess_return_20d",
    benchmark: str = "沪深300",
    commission: float = 0.0003,
    stamp_duty: float = 0.0005,
    slippage: float = 0.001,
    test_ratio: float = 0.2,
    validation_ratio: float = 0.2,
    workers: int | None = None,
    mode: str = "auto",
) -> BacktestSummary:
    if objective not in {
        "return_20d",
        "return_60d",
        "excess_return_20d",
        "excess_return_60d",
        "net_excess_return_20d",
        "net_excess_return_60d",
        "max_drawdown",
        "risk_adjusted",
    }:
        raise ValueError(f"unsupported objective: {objective}")
    if benchmark not in BENCHMARKS:
        raise ValueError(f"unsupported benchmark: {benchmark}")
    test_ratio = float(np.clip(test_ratio, 0.0, 0.9))
    validation_ratio = float(np.clip(validation_ratio, 0.0, 0.9 - test_ratio))
    benchmark_frame = _load_benchmark_frames(source).get(benchmark)
    benchmark_dates = pd.DatetimeIndex([])
    if benchmark_frame is not None and not benchmark_frame.empty:
        benchmark_dates = (
            pd.DatetimeIndex(benchmark_frame.index).dropna().sort_values().unique()
        )
    global_start = pd.Timestamp(benchmark_dates[0]) if len(benchmark_dates) else None
    global_end = pd.Timestamp(benchmark_dates[-1]) if len(benchmark_dates) else None
    if BACKTEST_VALIDATION_END or BACKTEST_TEST_START:
        validation_end = (
            pd.Timestamp(BACKTEST_VALIDATION_END)
            if BACKTEST_VALIDATION_END
            else None
        )
        test_start = pd.Timestamp(BACKTEST_TEST_START) if BACKTEST_TEST_START else None
    elif len(benchmark_dates):
        validation_index = int(
            len(benchmark_dates) * (1.0 - test_ratio - validation_ratio)
        )
        test_index = int(len(benchmark_dates) * (1.0 - test_ratio))
        validation_end = (
            pd.Timestamp(benchmark_dates[validation_index])
            if validation_ratio
            else None
        )
        test_start = pd.Timestamp(benchmark_dates[test_index]) if test_ratio else None
    else:
        validation_end = test_start = None
        if benchmark_frame is None or benchmark_frame.empty:
            summary = BacktestSummary(
                ticker_count=len(dict.fromkeys(tickers)),
                objective=objective,
                benchmark=benchmark,
                commission=commission,
                stamp_duty=stamp_duty,
                slippage=slippage,
                cost_parameters={
                    "commission": commission,
                    "stamp_duty": stamp_duty,
                    "slippage": slippage,
                },
                test_ratio=test_ratio,
                validation_ratio=validation_ratio,
                error=f"无法加载基准数据：{benchmark}，无法建立回测时间切分",
            )
            summary.insufficient_test_data = True
            return summary

    unique_tickers = list(dict.fromkeys(tickers))
    total = len(unique_tickers)
    profile = _resolve_backtest_profile(mode, total)
    sample_batches: list[pd.DataFrame] = []
    sample_count = 0
    worker_count = _adaptive_worker_count(total, workers, profile)
    use_process_pool = bool(
        total >= int(BACKTEST_PROCESS_MIN_TICKERS) and worker_count > 1
    )
    engine = "process" if use_process_pool else "sequential"
    benchmark_signature = "state-v5"
    completed = 0
    cache_hits = 0
    next_progress = max(1, int(BACKTEST_PROGRESS_INTERVAL))
    backtest_started = time.perf_counter()

    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

    logger.info(
        "Backtest engine: %s, mode=%s, workers=%d, chunk=%d, persistent cache=%s.",
        engine,
        profile.name.upper(),
        worker_count,
        profile.chunk_size,
        "on" if BACKTEST_CACHE_ENABLED else "off",
    )

    def record_progress(
        batch_frame: pd.DataFrame,
        batch_completed: int,
        batch_cache_hits: int,
    ) -> None:
        nonlocal completed, cache_hits, next_progress, sample_count
        if batch_frame is not None and not batch_frame.empty:
            sample_batches.append(batch_frame)
            sample_count += len(batch_frame)
        completed += int(batch_completed)
        cache_hits += int(batch_cache_hits)
        if completed >= next_progress or completed >= total:
            elapsed = max(time.perf_counter() - backtest_started, 1e-9)
            rate = completed / elapsed
            remaining = max(0, total - completed)
            eta = remaining / rate if rate > 0 else 0.0
            percent = completed / max(total, 1) * 100.0
            logger.info(
                "Backtesting progress: %d/%d tickers, %d samples. %.1f%% | mode=%s | cache=%d | elapsed=%s | ETA=%s | rate=%.2f ticker/s",
                completed,
                total,
                sample_count,
                percent,
                profile.name.upper(),
                cache_hits,
                _format_duration(elapsed),
                _format_duration(eta),
                rate,
            )
            interval = max(1, int(BACKTEST_PROGRESS_INTERVAL))
            next_progress = ((completed // interval) + 1) * interval

    if use_process_pool:
        chunk_size = max(1, int(profile.chunk_size))
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
                profile,
            ),
        ) as executor:
            futures = {
                executor.submit(_backtest_chunk_worker, chunk): chunk for chunk in chunks
            }
            for future in as_completed(futures):
                chunk = futures[future]
                try:
                    batch_frame, batch_hits, errors, batch_count = future.result()
                except Exception as exc:
                    logger.exception("Backtest worker chunk failed: %s", exc)
                    record_progress(pd.DataFrame(), len(chunk), 0)
                    continue
                for ticker, error in errors:
                    logger.warning("Backtest failed for %s: %s", ticker, error)
                record_progress(batch_frame, batch_count, batch_hits)
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
                    profile=profile,
                    benchmark_name=benchmark,
                )
            except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
                logger.warning("Backtest failed for %s: %s", ticker, exc)
                ticker_samples, cache_hit = [], False
            batch_frame = (
                pd.DataFrame.from_records(ticker_samples)
                if ticker_samples
                else pd.DataFrame()
            )
            record_progress(batch_frame, 1, int(cache_hit))

'''
analytics = replace_between(
    analytics,
    "_BACKTEST_WORKER_CONTEXT: dict[str, Any] = {}",
    "    split_dates = {",
    new_runner,
    "analytics runner",
)
analytics = replace_once(
    analytics,
    '''    summary = BacktestSummary(
        ticker_count=len(dict.fromkeys(tickers)),
        objective=objective,
''',
    '''    summary = BacktestSummary(
        ticker_count=len(dict.fromkeys(tickers)),
        mode=profile.name,
        objective=objective,
''',
    "summary resolved mode",
)
analytics = replace_once(
    analytics,
    '''    if not samples:
        summary.insufficient_test_data = True
        summary.error = "未生成有效回测样本"
    else:
        all_frame = pd.DataFrame(samples)
''',
    '''    if not sample_batches:
        summary.insufficient_test_data = True
        summary.error = "未生成有效回测样本"
    else:
        all_frame = pd.concat(sample_batches, ignore_index=True)
''',
    "summary sample batches",
)
write("analytics.py", analytics)


# ---------------------------------------------------------------------------
# main.py -- expose mode and auto selection
# ---------------------------------------------------------------------------
main = read("main.py")
main = replace_once(
    main,
    '''    logger.info("Backtesting %d explicitly specified tickers...", len(unique_tickers))
    options = {
''',
    '''    requested_mode = str(getattr(args, "mode", "auto") or "auto").lower()
    logger.info(
        "Backtesting %d explicitly specified tickers (mode=%s)...",
        len(unique_tickers),
        requested_mode.upper(),
    )
    options = {
''',
    "main backtest log",
)
main = replace_once(
    main,
    '''        workers=getattr(args, "workers", None),
        **options,
    )
''',
    '''        workers=getattr(args, "workers", None),
        mode=requested_mode,
        **options,
    )
''',
    "main mode pass",
)
main = replace_once(
    main,
    '''    logger.info(
        "Backtest complete: %d test samples, %d all samples, 20d win rate %.1f%%, average return %.2f%%, 60d average return %.2f%%.",
        summary.samples,
''',
    '''    logger.info(
        "Backtest complete: mode=%s, %d test samples, %d all samples, 20d win rate %.1f%%, average return %.2f%%, 60d average return %.2f%%.",
        str(getattr(summary, "mode", requested_mode)).upper(),
        summary.samples,
''',
    "main completion mode",
)
main = replace_once(
    main,
    '''    backtest_p.add_argument("--all-results", action="store_true")
    backtest_p.add_argument(
        "--workers", type=lambda value: _positive_int(value, "回测线程数"), default=None
    )
''',
    '''    backtest_p.add_argument("--all-results", action="store_true")
    backtest_p.add_argument(
        "--mode",
        choices=("auto", "fast", "exact"),
        default="auto",
        help="回测模式：auto=<=100只精确、>100只快速；fast=全市场快速；exact=最高精度",
    )
    backtest_p.add_argument(
        "--workers", type=lambda value: _positive_int(value, "回测进程数"), default=None
    )
''',
    "main parser mode",
)
write("main.py", main)


# ---------------------------------------------------------------------------
# gui_core.py -- Top50 Exact, large pool Fast, show mode in ETA status
# ---------------------------------------------------------------------------
gui = read("gui_core.py")
gui = replace_once(
    gui,
    '''BACKTEST_ETA_RE = re.compile(r"ETA=([^|]+)")
''',
    '''BACKTEST_ETA_RE = re.compile(r"ETA=([^|]+)")
BACKTEST_MODE_RE = re.compile(r"mode=(FAST|EXACT)")
''',
    "gui backtest mode regex",
)
gui = replace_once(
    gui,
    '''        command = [
            sys.executable,
            str(MAIN_FILE),
            "backtest",
            "--data-source",
            self._selected_data_source(),
            "--tickers-file",
            str(ticker_file),
        ]
        self.append_log(f"回测当前筛选结果：{len(backtest_tickers)} 个标的\\n")
        self.append_log(
            f"执行回测命令：{MAIN_FILE.name} backtest --数据源 {self.data_source.get()} --股票列表 BacktestAll.txt\\n"
        )
''',
    '''        backtest_mode = "exact" if len(backtest_tickers) <= 100 else "fast"
        command = [
            sys.executable,
            str(MAIN_FILE),
            "backtest",
            "--data-source",
            self._selected_data_source(),
            "--tickers-file",
            str(ticker_file),
            "--mode",
            backtest_mode,
        ]
        mode_label = "精确 Exact" if backtest_mode == "exact" else "快速 Fast"
        self.append_log(
            f"回测当前筛选结果：{len(backtest_tickers)} 个标的 · 模式：{mode_label}\\n"
        )
        self.append_log(
            f"执行回测命令：{MAIN_FILE.name} backtest --数据源 {self.data_source.get()} --股票列表 BacktestAll.txt --模式 {backtest_mode}\\n"
        )
''',
    "gui auto mode",
)
gui = replace_once(
    gui,
    '''        elif backtest_progress:
            completed, total, samples = (int(value) for value in backtest_progress.groups())
            eta_match = BACKTEST_ETA_RE.search(text)
            eta = eta_match.group(1).strip() if eta_match else "计算中"
            self.progress.stop()
            self.progress.configure(mode="determinate", maximum=max(total, 1), value=completed)
            self.status.set(f"历史回测 {completed}/{total} · 样本 {samples} · ETA {eta}")
''',
    '''        elif backtest_progress:
            completed, total, samples = (int(value) for value in backtest_progress.groups())
            eta_match = BACKTEST_ETA_RE.search(text)
            eta = eta_match.group(1).strip() if eta_match else "计算中"
            mode_match = BACKTEST_MODE_RE.search(text)
            mode = mode_match.group(1) if mode_match else ""
            mode_label = "精确" if mode == "EXACT" else "快速" if mode == "FAST" else "历史"
            self.progress.stop()
            self.progress.configure(mode="determinate", maximum=max(total, 1), value=completed)
            self.status.set(
                f"{mode_label}回测 {completed}/{total} · 样本 {samples} · ETA {eta}"
            )
''',
    "gui progress mode",
)
gui = replace_once(
    gui,
    '''            lines = [
                f"样本数：{data.get('samples', 0)}",
''',
    '''            lines = [
                f"回测模式：{str(data.get('mode', 'auto')).upper()}",
                f"样本数：{data.get('samples', 0)}",
''',
    "gui summary mode",
)
write("gui_core.py", gui)


# ---------------------------------------------------------------------------
# tests: validate auto Exact/Fast and incremental indicator extension
# ---------------------------------------------------------------------------
tests = read("test_performance_regressions.py")
tests = replace_once(
    tests,
    '''from config import (
    BACKTEST_CHUNK_SIZE,
    BACKTEST_MAX_PROCESSES,
    BACKTEST_PROGRESS_INTERVAL,
    BACKTEST_SCORE_WINDOW_BARS,
)
''',
    '''from config import (
    BACKTEST_AUTO_EXACT_MAX_TICKERS,
    BACKTEST_CHUNK_SIZE,
    BACKTEST_FAST_COOLDOWN_DAYS,
    BACKTEST_FAST_SCORE_WINDOW_BARS,
    BACKTEST_MAX_PROCESSES,
    BACKTEST_PROGRESS_INTERVAL,
    BACKTEST_SCORE_WINDOW_BARS,
)
''',
    "test imports",
)
tests = replace_once(
    tests,
    '''        self.assertGreaterEqual(BACKTEST_SCORE_WINDOW_BARS, 504)
        self.assertGreaterEqual(BACKTEST_MAX_PROCESSES, 2)
        self.assertGreaterEqual(BACKTEST_CHUNK_SIZE, 1)
        self.assertLessEqual(BACKTEST_PROGRESS_INTERVAL, 50)
''',
    '''        self.assertGreaterEqual(BACKTEST_SCORE_WINDOW_BARS, 504)
        self.assertGreaterEqual(BACKTEST_FAST_SCORE_WINDOW_BARS, 252)
        self.assertLessEqual(BACKTEST_FAST_SCORE_WINDOW_BARS, BACKTEST_SCORE_WINDOW_BARS)
        self.assertGreaterEqual(BACKTEST_FAST_COOLDOWN_DAYS, 20)
        self.assertEqual(BACKTEST_AUTO_EXACT_MAX_TICKERS, 100)
        self.assertGreaterEqual(BACKTEST_MAX_PROCESSES, 2)
        self.assertGreaterEqual(BACKTEST_CHUNK_SIZE, 1)
        self.assertLessEqual(BACKTEST_PROGRESS_INTERVAL, 50)
''',
    "test config assertions",
)
tests = replace_once(
    tests,
    '''        line = (
            "Backtesting progress: 250/5981 tickers, 422 samples. "
            "4.2% | cache=10 | elapsed=3m10s | ETA=1h02m | rate=1.30 ticker/s"
        )
''',
    '''        line = (
            "Backtesting progress: 250/5981 tickers, 422 samples. "
            "4.2% | mode=FAST | cache=10 | elapsed=3m10s | ETA=1h02m | rate=1.30 ticker/s"
        )
''',
    "test gui progress line",
)
tests = replace_once(
    tests,
    '''        self.assertEqual(gui_core.BACKTEST_ETA_RE.search(line).group(1).strip(), "1h02m")
''',
    '''        self.assertEqual(gui_core.BACKTEST_ETA_RE.search(line).group(1).strip(), "1h02m")
        self.assertEqual(gui_core.BACKTEST_MODE_RE.search(line).group(1), "FAST")

    def test_auto_mode_uses_exact_for_top50_and_fast_for_full_market(self):
        self.assertEqual(analytics._resolve_backtest_profile("auto", 50).name, "exact")
        self.assertEqual(analytics._resolve_backtest_profile("auto", 100).name, "exact")
        self.assertEqual(analytics._resolve_backtest_profile("auto", 101).name, "fast")
        self.assertEqual(analytics._resolve_backtest_profile("auto", 5985).name, "fast")
''',
    "test auto modes",
)
insert_marker = '''                pd.testing.assert_frame_equal(first, second, check_freq=False)
'''
insert = '''                pd.testing.assert_frame_equal(first, second, check_freq=False)

    def test_indicator_cache_incrementally_appends_new_daily_bar(self):
        base_index = pd.date_range("2026-01-01", periods=260, freq="B")
        base = pd.DataFrame(
            {
                "Open": range(260),
                "High": [value + 2 for value in range(260)],
                "Low": [max(0, value - 1) for value in range(260)],
                "Close": [value + 1 for value in range(260)],
                "Volume": [1000 + value for value in range(260)],
            },
            index=base_index,
            dtype=float,
        )
        extended = pd.concat(
            [
                base,
                pd.DataFrame(
                    {"Open": [260.0], "High": [262.0], "Low": [259.0], "Close": [261.0], "Volume": [1260.0]},
                    index=[base_index[-1] + pd.offsets.BDay(1)],
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "raw.parquet"
            source.write_bytes(b"raw")
            indicator_dir = Path(directory) / "indicators"
            with patch.object(performance_cache, "INDICATOR_CACHE_DIR", indicator_dir):
                calls: list[int] = []

                def compute(value):
                    calls.append(len(value))
                    result = value.copy()
                    result["MA20"] = result["Close"].rolling(20, min_periods=1).mean()
                    result["OBV"] = result["Volume"].cumsum()
                    result["AD"] = result["Volume"].cumsum() * 0.5
                    return result

                performance_cache.load_or_compute_indicators(
                    "000001.SZ", base, compute, source_path=source
                )
                source.write_bytes(b"raw-extended")
                result, reused = performance_cache.load_or_compute_indicators(
                    "000001.SZ", extended, compute, source_path=source
                )
                self.assertTrue(reused)
                self.assertEqual(len(result), len(extended))
                self.assertEqual(calls[0], len(base))
                self.assertLess(calls[1], len(extended))
'''
tests = replace_once(tests, insert_marker, insert, "incremental indicator test")
write("test_performance_regressions.py", tests)


# README performance notes
readme = read("README.md")
readme += '''\n### 回测模式与增量性能\n\n- GUI 当前筛选结果 **<=100 只自动使用 Exact**：504 根历史评分窗口、20 日信号冷却、历史时点 Volume Profile，供 Top50 最终精确验证。\n- **>100 只自动使用 Fast**：252 根评分窗口、40 日冷却、向量候选预筛、跳过逐历史点 Volume Profile，用于全市场粗校准。\n- TickFlow 日 K 只新增交易日时，指标缓存只计算尾部窗口；回测缓存只重算最近历史尾部并与旧样本合并。前复权历史发生变化时会通过 OHLCV 指纹自动退回全量重建。\n- 回测 worker 根据 CPU、任务规模和 Fast/Exact 模式自动选择，并使用 DataFrame 批次跨进程返回，减少大量 Python dict 的 IPC 开销。\n'''
write("README.md", readme)

print("Performance v2 migration applied.")
