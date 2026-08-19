"""v56 TickFlow authentication backed by local GUI settings.

The authenticated SDK path follows TickFlow's documented ``TickFlow(api_key=...)``
initialization.  A key saved from the GUI is kept only in the gitignored
``.env.local`` file and takes precedence over a process environment variable.
When no key is selected the existing TickFlow Free historical path remains the
fallback.
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Mapping
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

import downloader_v55 as _core
from downloader_v55 import *  # noqa: F403
from tickflow_settings import get_tickflow_api_key, get_tickflow_setting_source

_EOD_PREV_CLOSE_TOLERANCE = 0.02

# Bypass the v55 wrappers so v56 owns credential resolution and EOD repair while
# retaining the stable v51/v54 download/cache implementation underneath.
_V56_BASE_DOWNLOAD_BATCH = _core._V55_LEGACY_DOWNLOAD_BATCH
_V56_BASE_DOWNLOAD_TICKER = _core._V55_LEGACY_DOWNLOAD_TICKER
_V56_BASE_RECORD_MARKET_MANIFEST = _core._V55_LEGACY_RECORD_MARKET_MANIFEST
_V56_BASE_CLOSE_TICKFLOW_CLIENT = _core._V55_LEGACY_CLOSE_TICKFLOW_CLIENT
_core._TICKFLOW_CLIENT_MODE = getattr(_core, "_TICKFLOW_CLIENT_MODE", None)
_core._TICKFLOW_CLIENT_CREDENTIAL_ID = getattr(
    _core, "_TICKFLOW_CLIENT_CREDENTIAL_ID", None
)


def _tickflow_api_key() -> str:
    """Resolve GUI-local credentials first, then the process environment."""
    return get_tickflow_api_key()


def tickflow_api_enabled() -> bool:
    return bool(_tickflow_api_key())


def get_tickflow_client_mode() -> str:
    return "authenticated" if tickflow_api_enabled() else "free"


def get_data_source_label(source: str | None = None) -> str:
    """Expose the real runtime mode instead of always displaying 'Free'."""
    _core.normalize_data_source(source)
    return "TickFlow API" if tickflow_api_enabled() else "TickFlow Free"


def _credential_id(api_key: str) -> str:
    if not api_key:
        return "free"
    return "api:" + hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def _tickflow() -> Any:
    """Return a cached client whose credential identity matches current settings."""
    api_key = _tickflow_api_key()
    desired_mode = "authenticated" if api_key else "free"
    desired_credential = _credential_id(api_key)
    client = getattr(_core, "_TICKFLOW_CLIENT", None)
    current_mode = getattr(_core, "_TICKFLOW_CLIENT_MODE", None)
    current_credential = getattr(_core, "_TICKFLOW_CLIENT_CREDENTIAL_ID", None)
    if (
        client is not None
        and current_mode == desired_mode
        and current_credential == desired_credential
    ):
        return client

    if client is not None and hasattr(client, "close"):
        try:
            client.close()
        except Exception:
            _core.logger.debug("TickFlow client close failed", exc_info=True)
    _core._TICKFLOW_CLIENT = None
    _core._TICKFLOW_CLIENT_MODE = None
    _core._TICKFLOW_CLIENT_CREDENTIAL_ID = None

    if _core.TickFlow is None:
        raise _core.DownloadError(
            '未安装 TickFlow SDK；请运行 pip install "tickflow[all]==0.1.24"'
        )

    try:
        client = _core.TickFlow(api_key=api_key) if api_key else _core.TickFlow.free()
    except Exception as exc:
        label = "API" if api_key else "Free"
        message = str(exc).replace(api_key, "***") if api_key else str(exc)
        raise _core.DownloadError(f"TickFlow {label} 初始化失败: {message}") from exc

    _core._TICKFLOW_CLIENT = client
    _core._TICKFLOW_CLIENT_MODE = desired_mode
    _core._TICKFLOW_CLIENT_CREDENTIAL_ID = desired_credential
    _core.logger.info(
        "TickFlow client initialized in %s mode (credential source=%s).",
        desired_mode,
        get_tickflow_setting_source(),
    )
    return client


def close_tickflow_client() -> None:
    _V56_BASE_CLOSE_TICKFLOW_CLIENT()
    _core._TICKFLOW_CLIENT_MODE = None
    _core._TICKFLOW_CLIENT_CREDENTIAL_ID = None


def _finite_float(value: Any, *, allow_zero: bool = False) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(number):
        return None
    if allow_zero:
        return number if number >= 0.0 else None
    return number if number > 0.0 else None


def _quote_trade_date(row: Mapping[str, Any]) -> date | None:
    raw_date = row.get("trade_date") or row.get("date")
    if raw_date not in (None, ""):
        parsed = pd.to_datetime(raw_date, errors="coerce")
        if not pd.isna(parsed):
            stamp = pd.Timestamp(parsed)
            if stamp.tzinfo is not None:
                stamp = stamp.tz_convert("Asia/Shanghai")
            return stamp.date()

    raw_timestamp = row.get("timestamp")
    if raw_timestamp in (None, ""):
        return None
    try:
        numeric = float(raw_timestamp)
    except (TypeError, ValueError):
        parsed = pd.to_datetime(raw_timestamp, errors="coerce", utc=True)
    else:
        unit = "ms" if abs(numeric) >= 10_000_000_000 else "s"
        parsed = pd.to_datetime(numeric, unit=unit, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    stamp = pd.Timestamp(parsed)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    return stamp.tz_convert("Asia/Shanghai").date()


def _quote_records(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, pd.DataFrame):
        return [dict(row) for row in raw.to_dict(orient="records")]
    if isinstance(raw, Mapping):
        nested = raw.get("data")
        if isinstance(nested, (list, tuple)):
            return [mapped for item in nested if (mapped := _core._as_mapping(item))]
        return [dict(raw)]
    if isinstance(raw, (list, tuple)):
        return [mapped for item in raw if (mapped := _core._as_mapping(item))]
    return []


def _fetch_eod_quote_bars(
    symbols: list[str], target_day: date | None = None
) -> dict[str, pd.DataFrame]:
    """Use authenticated quotes to repair a daily bar after A-share close."""
    requested = list(
        dict.fromkeys(_core.normalize_ticker(symbol) for symbol in symbols if symbol)
    )
    if not requested or not tickflow_api_enabled() or not _core._is_a_share_market_closed():
        return {}

    target = target_day or _core._latest_completed_trading_day()
    client = _tickflow()
    try:
        raw = client.quotes.get(symbols=requested, as_dataframe=True)
    except TypeError:
        try:
            raw = client.quotes.get(symbols=requested)
        except Exception as exc:
            _core.logger.warning("TickFlow API 收盘快照获取失败: %s", exc)
            return {}
    except Exception as exc:
        _core.logger.warning("TickFlow API 收盘快照获取失败: %s", exc)
        return {}

    requested_set = set(requested)
    bars: dict[str, pd.DataFrame] = {}
    for row in _quote_records(raw):
        symbol = _core.normalize_ticker(str(row.get("symbol") or ""))
        if symbol not in requested_set or _quote_trade_date(row) != target:
            continue

        open_price = _finite_float(row.get("open"))
        high_price = _finite_float(row.get("high"))
        low_price = _finite_float(row.get("low"))
        close_price = _finite_float(row.get("last_price", row.get("close")))
        volume = _finite_float(row.get("volume"), allow_zero=True)
        amount = _finite_float(row.get("amount"), allow_zero=True)
        if None in (open_price, high_price, low_price, close_price, volume, amount):
            continue

        raw_daily = pd.DataFrame(
            {
                "trade_date": [target.isoformat()],
                "open": [open_price],
                "high": [high_price],
                "low": [low_price],
                "close": [close_price],
                "volume": [volume],
                "amount": [amount],
            }
        )
        normalized = _core._normalize_tickflow_frame(raw_daily)
        if normalized is None or normalized.empty:
            continue
        normalized.attrs["eod_quote_fallback"] = True
        normalized.attrs["eod_quote_trade_date"] = target.isoformat()
        normalized.attrs["eod_quote_source"] = "tickflow_api_quotes"
        prev_close = _finite_float(row.get("prev_close"))
        if prev_close is not None:
            normalized.attrs["eod_quote_prev_close"] = prev_close
        bars[symbol] = normalized

    if bars:
        _core.logger.info(
            "TickFlow API EOD quote fallback supplied %d/%d completed bars for %s.",
            len(bars),
            len(requested),
            target.isoformat(),
        )
    return bars


def _quote_history_is_compatible(
    history: pd.DataFrame, quote_bar: pd.DataFrame
) -> bool:
    prev_close = _finite_float(quote_bar.attrs.get("eod_quote_prev_close"))
    if prev_close is None or history.empty or "Close" not in history.columns:
        return True
    historical_close = _finite_float(history["Close"].iloc[-1])
    if historical_close is None:
        return False
    discrepancy = abs(historical_close / prev_close - 1.0)
    if discrepancy <= _EOD_PREV_CLOSE_TOLERANCE:
        return True
    _core.logger.warning(
        "TickFlow API EOD quote fallback skipped: previous-close mismatch %.2f%% suggests an unsettled corporate-action rebase.",
        discrepancy * 100.0,
    )
    return False


def _augment_with_eod_quotes(
    frames: Mapping[str, pd.DataFrame],
) -> tuple[dict[str, pd.DataFrame], set[str]]:
    result = {str(symbol): frame for symbol, frame in frames.items()}
    if not result or not tickflow_api_enabled() or not _core._is_a_share_market_closed():
        return result, set()

    stale = [
        _core.normalize_ticker(symbol)
        for symbol, frame in result.items()
        if not _core._cache_has_completed_daily_bar(frame)
    ]
    if not stale:
        return result, set()

    quote_bars = _fetch_eod_quote_bars(stale)
    changed: set[str] = set()
    for symbol in stale:
        history = result.get(symbol)
        quote_bar = quote_bars.get(symbol)
        if history is None or quote_bar is None:
            continue
        if not _quote_history_is_compatible(history, quote_bar):
            continue
        merged = _core._merge_cached(history, quote_bar)
        if not _core._cache_has_completed_daily_bar(merged):
            continue
        merged.attrs.update(quote_bar.attrs)
        result[symbol] = merged
        changed.add(symbol)
    return result, changed


def _record_market_manifest(ticker: str, df: pd.DataFrame) -> None:
    _V56_BASE_RECORD_MARKET_MANIFEST(ticker, df)
    key = _core.normalize_ticker(ticker)
    row = _core._MARKET_MANIFEST_DIRTY.get(key)
    if not isinstance(row, dict):
        return
    row["tickflow_client_mode"] = get_tickflow_client_mode()
    row["tickflow_credential_source"] = get_tickflow_setting_source()
    row["eod_quote_fallback"] = bool(df.attrs.get("eod_quote_fallback", False))
    row["eod_quote_trade_date"] = str(df.attrs.get("eod_quote_trade_date", "") or "")
    row["eod_quote_source"] = str(df.attrs.get("eod_quote_source", "") or "")


def download_ticker(
    ticker: str,
    force: bool = False,
    source: str | None = None,
    cache_first: bool = False,
) -> pd.DataFrame | None:
    frame = _V56_BASE_DOWNLOAD_TICKER(
        ticker,
        force=force,
        source=source,
        cache_first=cache_first,
    )
    if frame is None or frame.empty or cache_first:
        return frame

    symbol = _core.normalize_ticker(ticker)
    augmented, changed = _augment_with_eod_quotes({symbol: frame})
    result = augmented.get(symbol, frame)
    if symbol in changed:
        _core._save_cache(symbol, result, source)
        _record_market_manifest(symbol, result)
        _core._flush_market_manifest()
    return result


def download_batch(
    tickers: list[TickerInfo],
    desc: str = "Downloading",
    force: bool = False,
    source: str | None = None,
    cache_first: bool = False,
    skip_tickers: set[str] | None = None,
    progress_callback: DownloadProgressCallback | None = None,
) -> dict[str, pd.DataFrame]:
    frames = _V56_BASE_DOWNLOAD_BATCH(
        tickers,
        desc=desc,
        force=force,
        source=source,
        cache_first=cache_first,
        skip_tickers=skip_tickers,
        progress_callback=progress_callback,
    )
    if not frames or cache_first:
        return frames

    augmented, changed = _augment_with_eod_quotes(frames)
    if changed:
        for symbol in changed:
            frame = augmented[symbol]
            _core._save_cache(symbol, frame, source)
            _record_market_manifest(symbol, frame)
        _core._flush_market_manifest()
        _core.logger.info(
            "Applied authenticated TickFlow API EOD close fallback to %d tickers.",
            len(changed),
        )
    return augmented


# Install v56 at the stable shared downloader module used by scanner/GUI/CLI.
_core._tickflow_api_key = _tickflow_api_key
_core.tickflow_api_enabled = tickflow_api_enabled
_core.get_tickflow_client_mode = get_tickflow_client_mode
_core.get_data_source_label = get_data_source_label
_core._tickflow = _tickflow
_core.close_tickflow_client = close_tickflow_client
_core._quote_trade_date = _quote_trade_date
_core._fetch_eod_quote_bars = _fetch_eod_quote_bars
_core._quote_history_is_compatible = _quote_history_is_compatible
_core._augment_with_eod_quotes = _augment_with_eod_quotes
_core._record_market_manifest = _record_market_manifest
_core.download_ticker = download_ticker
_core.download_batch = download_batch

sys.modules[__name__] = _core
