"""v82 point-in-time stock-universe snapshot persistence.

``historical_universe.py`` already knows how to *consume* dated eligibility
snapshots, but prior releases did not create them during normal operation. A
successful full-market stock scan can therefore start building prospective
survivorship-bias evidence without changing any score or historical sample.

Snapshots are deliberately derived from the canonical published full result
set, use its dominant ``DataAsOf`` market date, and are written atomically.
Manual ticker subsets are filtered by the caller and must never be persisted as
if they were a complete market universe. ETF rows are explicitly excluded even
though their six-digit exchange symbols match the generic security regex.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pandas as pd

import historical_universe as _historical
from downloader import normalize_ticker

UNIVERSE_SNAPSHOT_VERSION = "2026-08-21-v82-prospective-universe-snapshot-v2"


def _truthy(values: pd.Series, default: bool = True) -> pd.Series:
    if pd.api.types.is_bool_dtype(values):
        return values.fillna(default).astype(bool)
    return (
        values.fillna(default)
        .astype(str)
        .str.strip()
        .str.lower()
        .isin({"true", "1", "yes", "y", "是"})
    )


def _is_etf(frame: pd.DataFrame) -> pd.Series:
    result = pd.Series(False, index=frame.index, dtype=bool)
    if "IsETF" in frame.columns:
        result |= _truthy(frame["IsETF"], False)
    if "AssetType" in frame.columns:
        result |= frame["AssetType"].fillna("").astype(str).str.strip().str.lower().eq(
            "etf"
        )
    return result


def _dominant_market_date(frame: pd.DataFrame) -> pd.Timestamp | None:
    if "DataAsOf" not in frame.columns:
        return None
    dates = pd.to_datetime(frame["DataAsOf"], errors="coerce").dropna()
    if dates.empty:
        return None
    normalized = dates.dt.normalize()
    counts = normalized.value_counts()
    if counts.empty:
        return None
    return pd.Timestamp(counts.index[0])


def _snapshot_frame(frame: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    if "Ticker" not in frame.columns:
        raise ValueError("canonical result set is missing Ticker")

    ticker = frame["Ticker"].fillna("").astype(str).map(normalize_ticker)
    security = ticker.map(
        lambda value: bool(_historical._SECURITY.fullmatch(value))
    )
    stock_security = security & ~_is_etf(frame)
    if "UniverseEligible" in frame.columns:
        eligible = _truthy(frame["UniverseEligible"], True)
    else:
        eligible = pd.Series(True, index=frame.index, dtype=bool)

    reason_column = next(
        (
            column
            for column in (
                "UniverseExclusionReason",
                "ExclusionReason",
                "EligibilityReason",
            )
            if column in frame.columns
        ),
        None,
    )
    reason = (
        frame[reason_column].fillna("").astype(str)
        if reason_column is not None
        else pd.Series("", index=frame.index, dtype=object)
    )

    output = pd.DataFrame(
        {
            "AsOf": as_of.date().isoformat(),
            "Ticker": ticker,
            "Eligible": eligible,
            "ExclusionReason": reason,
            "SnapshotVersion": UNIVERSE_SNAPSHOT_VERSION,
        }
    )
    output = output.loc[stock_security & output["Ticker"].ne("")]
    output = output.drop_duplicates(subset=["Ticker"], keep="last")
    return output.sort_values("Ticker").reset_index(drop=True)


def record_universe_snapshot(
    frame: pd.DataFrame,
    *,
    snapshot_dir: Path | None = None,
    as_of: str | pd.Timestamp | None = None,
) -> Path | None:
    """Atomically persist one complete stock-market snapshot.

    ``None`` is returned when no trustworthy market date or no stock-security
    rows are available. The caller decides whether a scan is a complete market
    scan; this function intentionally does not infer completeness from row
    count alone.
    """
    if frame is None or frame.empty:
        return None
    market_date = (
        pd.Timestamp(as_of)
        if as_of is not None
        else _dominant_market_date(frame)
    )
    if market_date is None or pd.isna(market_date):
        return None

    snapshot = _snapshot_frame(frame, pd.Timestamp(market_date))
    if snapshot.empty:
        return None

    destination = Path(snapshot_dir or _historical._SNAPSHOT_DIR)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"{pd.Timestamp(market_date).date().isoformat()}.csv"
    temporary = destination / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        snapshot.to_csv(temporary, index=False, encoding="utf-8-sig")
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    _historical._load_snapshot_index.cache_clear()
    return target


def record_universe_snapshot_file(
    result_path: str | Path,
    *,
    snapshot_dir: Path | None = None,
) -> Path | None:
    """Persist a snapshot from a canonical CSV or Parquet result file."""
    path = Path(result_path)
    if not path.exists():
        return None
    if path.suffix.lower() == ".parquet":
        frame = pd.read_parquet(path)
    else:
        frame = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    return record_universe_snapshot(frame, snapshot_dir=snapshot_dir)
