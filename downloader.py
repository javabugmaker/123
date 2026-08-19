"""TickFlow Free public facade with post-close daily-bar settlement retry.

The stable v51/v54 downloader remains the only market-data implementation.
TickFlow Free supplies the universe and historical daily OHLCV.  After the
A-share market closes, a provider may need a short settlement window before all
symbols expose the just-completed daily bar.  This facade retries only symbols
whose cached history is still behind the latest completed trading day.

No API key, authenticated client, realtime quote endpoint, minute data or
synthetic OHLCV construction is used here.  If TickFlow Free still has not
settled a mixed-date universe after the bounded retries, the download phase
fails before the expensive analysis stage.  A coherent provider-wide one-day
lag remains allowed for the existing v53 DAILY provenance contract.
"""

from __future__ import annotations

import sys
import time
from functools import lru_cache

import pandas as pd

import downloader_v51 as _core
from downloader_v51 import *  # noqa: F403

_v51_price_limit = _core.get_price_limit_pct
_FREE_EOD_LEGACY_DOWNLOAD_BATCH = _core.download_batch
_FREE_EOD_LEGACY_DOWNLOAD_TICKER = _core.download_ticker

# The base downloader has already made one normal refresh request.  Give the
# Free service up to ~48 additional seconds to finish its post-close daily-bar
# settlement before deciding whether the date distribution is still unsafe.
_FREE_EOD_RETRY_ATTEMPTS = 5
_FREE_EOD_RETRY_PAUSE_SECONDS = 12.0
_FREE_EOD_MIN_COHERENT_RATIO = 0.90


def _cached_metadata_available(ticker: str) -> bool:
    symbol = _core.normalize_ticker(ticker)
    metadata = _core._INSTRUMENT_META.get(symbol)
    if isinstance(metadata, dict) and metadata:
        return True
    cached = _core._load_universe_cache()
    if not isinstance(cached, dict):
        return False
    metadata_map = cached.get("metadata", {})
    if not isinstance(metadata_map, dict):
        return False
    raw = metadata_map.get(symbol)
    if not isinstance(raw, dict) or not raw:
        return False
    _core._INSTRUMENT_META[symbol] = raw
    return True


@lru_cache(maxsize=8192)
def get_price_limit_pct(ticker: str, is_etf: bool = False) -> float | None:
    """Resolve a security limit from cached TickFlow metadata only."""
    if not _cached_metadata_available(ticker):
        return None
    return _v51_price_limit(ticker, is_etf=is_etf)


def _frame_latest_date(frame: pd.DataFrame | None) -> str:
    if frame is None or frame.empty:
        return ""
    index = pd.DatetimeIndex(pd.to_datetime(frame.index, errors="coerce")).dropna()
    if index.empty:
        return ""
    stamp = pd.Timestamp(index.max())
    if stamp.tzinfo is not None:
        stamp = stamp.tz_localize(None)
    return stamp.date().isoformat()


def _stale_free_symbols(frames: dict[str, pd.DataFrame]) -> list[str]:
    return [
        _core.normalize_ticker(symbol)
        for symbol, frame in frames.items()
        if not _core._cache_has_completed_daily_bar(frame)
    ]


def _free_date_distribution(frames: dict[str, pd.DataFrame]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for frame in frames.values():
        value = _frame_latest_date(frame)
        if value:
            counts[value] = counts.get(value, 0) + 1
    return counts


def _assert_free_eod_coherence(frames: dict[str, pd.DataFrame]) -> None:
    """Abort early only for unsafe mixed-date settlement after market close.

    v53 intentionally allows a coherent provider-wide one-trading-day lag.  We
    preserve that behavior.  The unsafe case is a split universe such as 73%
    today / 27% yesterday, where ranking different securities on different
    market dates would be invalid.
    """
    if not frames or not _core._is_a_share_market_closed():
        return
    target = _core._latest_completed_trading_day().isoformat()
    counts = _free_date_distribution(frames)
    if not counts:
        return
    total = len(frames)
    fresh = counts.get(target, 0)
    fresh_ratio = fresh / max(1, total)
    dominant_date, dominant_count = max(counts.items(), key=lambda item: item[1])
    dominant_ratio = dominant_count / max(1, total)

    if fresh_ratio >= _FREE_EOD_MIN_COHERENT_RATIO:
        return
    if dominant_date != target and dominant_ratio >= _FREE_EOD_MIN_COHERENT_RATIO:
        _core.logger.warning(
            "TickFlow Free 当前仍为一致性供应商延迟: 主日期 %s 覆盖 %.1f%%，"
            "目标交易日 %s 覆盖 %.1f%%；保留 v53 PROVIDER_LAG 语义。",
            dominant_date,
            dominant_ratio * 100.0,
            target,
            fresh_ratio * 100.0,
        )
        return

    distribution = ", ".join(
        f"{day}={count}" for day, count in sorted(counts.items(), reverse=True)[:5]
    )
    raise _core.DownloadError(
        "TickFlow Free 盘后日K仍处于混合结算状态："
        f"目标交易日 {target} 覆盖 {fresh_ratio:.1%}，"
        f"主日期 {dominant_date} 覆盖 {dominant_ratio:.1%}（{distribution}）。"
        "已停止后续分析，请稍后重新扫描；不会混用不同交易日排名。"
    )


def _refresh_free_eod_frames(
    frames: dict[str, pd.DataFrame],
    *,
    source: str | None = None,
    attempts: int = _FREE_EOD_RETRY_ATTEMPTS,
    pause_seconds: float = _FREE_EOD_RETRY_PAUSE_SECONDS,
) -> dict[str, pd.DataFrame]:
    """Retry unsettled completed-day bars through TickFlow Free only.

    ``download_batch`` has already performed the normal incremental refresh.
    This function is therefore deliberately narrow: it runs only after market
    close, only for frames still behind the completed trading day, and only via
    the existing Free daily K-line batch endpoint.
    """
    result = {
        _core.normalize_ticker(symbol): frame
        for symbol, frame in frames.items()
        if frame is not None and not frame.empty
    }
    if not result or not _core._is_a_share_market_closed():
        return result

    stale = _stale_free_symbols(result)
    if not stale:
        return result

    target = _core._latest_completed_trading_day()
    total = len(result)
    _core.logger.warning(
        "TickFlow Free 当日日K尚未完全结算: %d/%d 标的仍早于 %s；开始盘后重试。",
        len(stale),
        total,
        target.isoformat(),
    )

    attempts = max(1, int(attempts))
    pause_seconds = max(0.0, float(pause_seconds))
    changed: set[str] = set()

    for attempt in range(1, attempts + 1):
        if attempt > 1 and pause_seconds > 0.0:
            time.sleep(pause_seconds)

        # Recreate the Free client between settlement attempts so a stale HTTP
        # connection/session cannot pin us to an earlier provider snapshot.
        _core.close_tickflow_client()
        refreshed: dict[str, pd.DataFrame] = {}
        for chunk in _core._request_chunks(stale):
            try:
                refreshed.update(_core._batch_fetch(chunk, _core._INCREMENTAL_BARS))
            except _core.DownloadError as exc:
                _core.logger.warning(
                    "TickFlow Free 盘后重试 %d/%d 批量日K失败: %s",
                    attempt,
                    attempts,
                    exc,
                )

        for symbol in list(stale):
            recent = refreshed.get(symbol)
            if recent is None or recent.empty:
                continue
            if not _core._cache_has_completed_daily_bar(recent):
                continue

            history = result.get(symbol)
            if history is None or history.empty:
                candidate = recent
            elif _core._requires_full_rebase(history, recent):
                # A forward-adjustment rebase must be rebuilt from TickFlow
                # itself; never append a new raw row onto an incompatible base.
                candidate = _core._fetch_one(symbol)
                if candidate is None or candidate.empty:
                    continue
            else:
                candidate = _core._merge_cached(history, recent)

            if candidate is None or candidate.empty:
                continue
            if not _core._cache_has_completed_daily_bar(candidate):
                continue

            candidate.attrs["free_eod_settlement_retry"] = True
            candidate.attrs["free_eod_target_date"] = target.isoformat()
            result[symbol] = candidate
            _core._save_cache(symbol, candidate, source)
            _core._record_market_manifest(symbol, candidate)
            changed.add(symbol)

        stale = _stale_free_symbols(result)
        _core.logger.info(
            "TickFlow Free 盘后结算重试 %d/%d: 已补齐 %d，仍待结算 %d，目标交易日 %s。",
            attempt,
            attempts,
            len(changed),
            len(stale),
            target.isoformat(),
        )
        if not stale:
            break

    if changed:
        _core._flush_market_manifest()

    if stale:
        date_counts: dict[str, int] = {}
        for symbol in stale:
            value = _frame_latest_date(result.get(symbol)) or "无日期"
            date_counts[value] = date_counts.get(value, 0) + 1
        distribution = ", ".join(
            f"{day}={count}" for day, count in sorted(date_counts.items(), reverse=True)[:5]
        )
        _core.logger.warning(
            "TickFlow Free 盘后日K仍未完全结算: %d/%d 标的未到 %s（%s）。"
            "不会伪造当日K线。",
            len(stale),
            total,
            target.isoformat(),
            distribution or "无可用日期",
        )
    else:
        _core.logger.info(
            "TickFlow Free 当日日K结算完成: %d/%d 标的已覆盖 %s。",
            total,
            total,
            target.isoformat(),
        )
    return result


def download_ticker(
    ticker: str,
    force: bool = False,
    source: str | None = None,
    cache_first: bool = False,
) -> pd.DataFrame | None:
    frame = _core._FREE_EOD_LEGACY_DOWNLOAD_TICKER(
        ticker,
        force=force,
        source=source,
        cache_first=cache_first,
    )
    if frame is None or frame.empty or cache_first:
        return frame
    symbol = _core.normalize_ticker(ticker)
    return _core._refresh_free_eod_frames({symbol: frame}, source=source).get(symbol, frame)


def download_batch(
    tickers: list[TickerInfo],
    desc: str = "Downloading",
    force: bool = False,
    source: str | None = None,
    cache_first: bool = False,
    skip_tickers: set[str] | None = None,
    progress_callback: DownloadProgressCallback | None = None,
) -> dict[str, pd.DataFrame]:
    frames = _core._FREE_EOD_LEGACY_DOWNLOAD_BATCH(
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
    refreshed = _core._refresh_free_eod_frames(frames, source=source)
    _core._assert_free_eod_coherence(refreshed)
    return refreshed


_core.get_price_limit_pct = get_price_limit_pct
_core._frame_latest_date = _frame_latest_date
_core._stale_free_symbols = _stale_free_symbols
_core._free_date_distribution = _free_date_distribution
_core._assert_free_eod_coherence = _assert_free_eod_coherence
_core._refresh_free_eod_frames = _refresh_free_eod_frames
_core._FREE_EOD_LEGACY_DOWNLOAD_BATCH = _FREE_EOD_LEGACY_DOWNLOAD_BATCH
_core._FREE_EOD_LEGACY_DOWNLOAD_TICKER = _FREE_EOD_LEGACY_DOWNLOAD_TICKER
_core.download_ticker = download_ticker
_core.download_batch = download_batch
sys.modules[__name__] = _core
