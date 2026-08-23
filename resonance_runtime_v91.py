"""v91 parent-process resonance recovery and vectorized aggregation bridge.

The v90 worker-side diagnostic wrapper can be bypassed by later acceleration
installers inside spawned Windows workers. Production scoring remains correct,
but the parent may receive return samples without ``resonance_count`` and then
publish zero diagnostic groups. v91 repairs that observability gap at the stable
parent aggregation boundary.

Only the security partition requires iteration because every ticker has an
independent OHLCV file. Within each partition, signal-date alignment is a
vectorized merge and no per-sample Python loop is used.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import numpy as np
import pandas as pd

import analytics_core as _core
from technical_resonance_v90 import attach_resonance_to_sample_frame

RUNTIME_VERSION = "2026-08-23-v91-parent-resonance-vectorized-v1"
_DEFAULT_SOURCE = "tickflow"
_INSTALLED = False
_PREVIOUS_TICKER_BACKTEST_ROWS: Callable[
    [pd.DataFrame, str], list[dict[str, Any]]
] | None = None
logger = logging.getLogger("institution_scanner.resonance_runtime")


def _normalized_ticker(frame: pd.DataFrame) -> pd.Series:
    if "ticker" not in frame.columns:
        return pd.Series("", index=frame.index, dtype=object)
    return (
        frame["ticker"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )


def _source_for_samples(frame: pd.DataFrame) -> str:
    for column in ("data_source", "source", "DataSource"):
        if column not in frame.columns:
            continue
        values = (
            frame[column]
            .dropna()
            .astype(str)
            .str.strip()
            .str.lower()
        )
        values = values.loc[values.ne("")]
        if not values.empty:
            return str(values.iloc[0])
    return _DEFAULT_SOURCE


def _existing_resonance_count(frame: pd.DataFrame) -> int:
    if "resonance_count" not in frame.columns:
        return 0
    count = pd.to_numeric(frame["resonance_count"], errors="coerce")
    return int(count.between(0, 5, inclusive="both").sum())


def ensure_parent_resonance(
    sample_frame: pd.DataFrame,
) -> pd.DataFrame:
    """Recover missing v90 diagnostics from local OHLCV caches in the parent.

    If any valid resonance observations already exist, the worker path is
    trusted. The fallback is only activated for the real failure mode observed
    in production: a non-empty held-out sample frame with zero resonance rows.
    """
    if sample_frame is None or sample_frame.empty:
        return sample_frame.copy()
    if _existing_resonance_count(sample_frame) > 0:
        return sample_frame

    tickers = _normalized_ticker(sample_frame)
    if tickers.eq("").all() or "signal_date" not in sample_frame.columns:
        return sample_frame

    source = _source_for_samples(sample_frame)
    working = sample_frame.copy()
    working["_v91_position"] = np.arange(len(working), dtype=np.int64)
    working["_v91_ticker"] = tickers.to_numpy()
    groups = working.groupby("_v91_ticker", sort=False, observed=True).indices

    parts: list[pd.DataFrame] = []
    recovered_tickers = 0
    for ticker, positions in groups.items():
        ticker_text = str(ticker).strip().upper()
        subset = working.iloc[np.asarray(positions, dtype=np.int64)].copy()
        if not ticker_text:
            parts.append(subset)
            continue
        try:
            market = _core._load_cache(ticker_text, source)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            logger.debug("Resonance cache load failed for %s: %s", ticker_text, exc)
            parts.append(subset)
            continue
        if market is None or market.empty:
            parts.append(subset)
            continue
        try:
            enriched = attach_resonance_to_sample_frame(subset, market)
        except (ArithmeticError, KeyError, TypeError, ValueError) as exc:
            logger.debug("Resonance recovery failed for %s: %s", ticker_text, exc)
            parts.append(subset)
            continue
        parts.append(enriched)
        if _existing_resonance_count(enriched) > 0:
            recovered_tickers += 1

    if not parts:
        return sample_frame

    recovered = pd.concat(parts, axis=0, copy=False)
    recovered = recovered.sort_values("_v91_position", kind="stable")
    recovered = recovered.drop(columns=["_v91_position", "_v91_ticker"])
    recovered.index = sample_frame.index

    valid = _existing_resonance_count(recovered)
    if valid:
        logger.info(
            "v91 parent resonance recovery: %d/%d held-out samples across %d tickers.",
            valid,
            len(recovered),
            recovered_tickers,
        )
    else:
        logger.warning(
            "v91 parent resonance recovery found no full five-factor samples "
            "across %d held-out rows; diagnostics remain empty.",
            len(recovered),
        )
    return recovered


def _ticker_backtest_rows_v91(
    sample_frame: pd.DataFrame,
    objective: str = "net_excess_return_20d",
) -> list[dict[str, Any]]:
    previous = _PREVIOUS_TICKER_BACKTEST_ROWS
    if previous is None:
        raise RuntimeError("v91 resonance runtime installed without previous ranker")
    return previous(ensure_parent_resonance(sample_frame), objective)


def install() -> None:
    """Install once after the public analytics facade has installed v90."""
    global _INSTALLED, _PREVIOUS_TICKER_BACKTEST_ROWS
    if _INSTALLED:
        return
    current = _core._ticker_backtest_rows
    if current is _ticker_backtest_rows_v91:
        _INSTALLED = True
        return
    _PREVIOUS_TICKER_BACKTEST_ROWS = current
    _core._ticker_backtest_rows = _ticker_backtest_rows_v91
    _core.BACKTEST_RESONANCE_RUNTIME_VERSION = RUNTIME_VERSION
    _INSTALLED = True


install()
