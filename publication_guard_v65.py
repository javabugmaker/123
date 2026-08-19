"""v65 canonical publication guard for cache-first scans.

``cache_first`` intentionally avoids network refresh, but canonical output files
must not be replaced by an arbitrarily old cache snapshot.  This guard applies
the same market-date philosophy as DAILY publication before ``export_all``:

* future-dated rows always fail closed;
* at least 90% of successful rows must share either the latest completed session
  or one coherent provider-lag session;
* the coherent provider lag may be at most the configured one trading day.

Research/scoring values are not changed.  A stale cache-first run simply keeps
the previous published files intact and tells the caller to perform a normal
refresh.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any

from config import (
    DAILY_MAX_PROVIDER_LAG_TRADING_DAYS,
    DAILY_MIN_COHERENT_DATA_DATE_RATIO,
)
from trading_calendar import is_trading_day, latest_completed_trading_day


def _text(value: Any) -> str:
    return str(value or "").strip()


def _result_date(result: Any) -> date | None:
    value = _text(getattr(result, "data_asof", ""))
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _trading_lag(asof: date, target: date) -> int:
    if asof > target:
        return -1
    if asof == target:
        return 0
    count = 0
    cursor = asof + timedelta(days=1)
    while cursor <= target:
        if is_trading_day(cursor):
            count += 1
        cursor += timedelta(days=1)
    return count


def enforce_cache_first_market_contract(
    results: list[Any],
    *,
    expected_date: date | None = None,
) -> dict[str, Any]:
    """Raise before export when a cache-first result set is materially stale."""
    target = expected_date or latest_completed_trading_day()
    successful = [result for result in results if not _text(getattr(result, "error", ""))]
    if not successful:
        raise ValueError("CACHE_FIRST_MARKET_CONTRACT_FAILED: no successful rows")

    dates = [_result_date(result) for result in successful]
    future = [value for value in dates if value is not None and value > target]
    if future:
        newest = max(future)
        raise ValueError(
            "CACHE_FIRST_MARKET_CONTRACT_FAILED: "
            f"future market date {newest.isoformat()} > completed session {target.isoformat()}"
        )

    counts = Counter(value for value in dates if value is not None)
    if not counts:
        raise ValueError(
            "CACHE_FIRST_MARKET_CONTRACT_FAILED: successful rows have no DataAsOf"
        )

    total = len(successful)
    fresh_count = counts.get(target, 0)
    fresh_ratio = fresh_count / max(1, total)
    dominant_date, dominant_count = max(counts.items(), key=lambda item: item[1])
    dominant_ratio = dominant_count / max(1, total)
    minimum_ratio = float(DAILY_MIN_COHERENT_DATA_DATE_RATIO)
    max_lag = max(0, int(DAILY_MAX_PROVIDER_LAG_TRADING_DAYS))

    if fresh_ratio >= minimum_ratio:
        return {
            "status": "CURRENT",
            "target": target.isoformat(),
            "dominant_date": dominant_date.isoformat(),
            "dominant_ratio": round(dominant_ratio, 6),
            "lag_trading_days": 0,
        }

    if dominant_ratio < minimum_ratio:
        distribution = ", ".join(
            f"{day.isoformat()}={count}"
            for day, count in sorted(counts.items(), reverse=True)[:5]
        )
        raise ValueError(
            "CACHE_FIRST_MARKET_CONTRACT_FAILED: mixed market dates; "
            f"target {target.isoformat()}={fresh_ratio:.1%}, "
            f"dominant {dominant_date.isoformat()}={dominant_ratio:.1%} "
            f"({distribution})"
        )

    lag = _trading_lag(dominant_date, target)
    if lag < 0 or lag > max_lag:
        raise ValueError(
            "CACHE_FIRST_MARKET_CONTRACT_FAILED: coherent cache is too stale; "
            f"dominant={dominant_date.isoformat()} target={target.isoformat()} "
            f"lag={lag} trading days (max={max_lag})"
        )

    return {
        "status": "PROVIDER_LAG",
        "target": target.isoformat(),
        "dominant_date": dominant_date.isoformat(),
        "dominant_ratio": round(dominant_ratio, 6),
        "lag_trading_days": lag,
    }
