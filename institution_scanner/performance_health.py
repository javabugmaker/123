"""Comparable DAILY runtime regression diagnostics.

Performance changes are diagnostic-only. A previous run is a valid timing
baseline only when the requested backtest mode, universe size and cache state are
materially comparable; otherwise a ratio can be misleading after a cold start or
universe migration.
"""
from __future__ import annotations

from typing import Final

PERFORMANCE_HEALTH_VERSION: Final = "2026-08-25-v108.3-comparable-runtime-health-v1"
_MAX_UNIVERSE_DELTA_RATIO: Final = 0.05
_MAX_CACHE_HIT_DELTA: Final = 0.25
_REGRESSION_RATIO: Final = 1.30
_IMPROVEMENT_RATIO: Final = 0.85


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _number(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed >= 0.0 else 0.0


def _integer(value: object) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def _metric(current: float, previous: float) -> dict[str, float | None]:
    delta = current - previous
    ratio = current / previous if previous > 0.0 else None
    return {
        "current_seconds": round(current, 3),
        "previous_seconds": round(previous, 3),
        "delta_seconds": round(delta, 3),
        "ratio": round(ratio, 4) if ratio is not None else None,
    }


def build_performance_health(
    current: dict[str, object],
    previous: dict[str, object],
) -> dict[str, object]:
    """Compare two DAILY manifests without turning noisy runs into regressions."""
    current_scan = _mapping(current.get("scan_breakdown"))
    previous_scan = _mapping(previous.get("scan_breakdown"))
    current_backtest = _mapping(current.get("backtest"))
    previous_backtest = _mapping(previous.get("backtest"))
    current_universe = _mapping(current.get("universe"))
    previous_universe = _mapping(previous.get("universe"))

    current_mode = str(current.get("requested_mode", "") or "").upper()
    previous_mode = str(previous.get("requested_mode", "") or "").upper()
    current_rows = _integer(current_universe.get("rows"))
    previous_rows = _integer(previous_universe.get("rows"))
    current_cache = _number(current_backtest.get("cache_hit_rate"))
    previous_cache = _number(previous_backtest.get("cache_hit_rate"))

    reasons: list[str] = []
    if not previous or _number(previous.get("elapsed_seconds")) <= 0.0:
        reasons.append("missing_previous_runtime_baseline")
    if current_mode and previous_mode and current_mode != previous_mode:
        reasons.append(f"mode_changed:{previous_mode}->{current_mode}")
    if current_rows > 0 and previous_rows > 0:
        universe_delta = abs(current_rows - previous_rows) / previous_rows
        if universe_delta > _MAX_UNIVERSE_DELTA_RATIO:
            reasons.append(f"universe_changed:{universe_delta:.1%}")
    if abs(current_cache - previous_cache) > _MAX_CACHE_HIT_DELTA:
        reasons.append(f"cache_hit_rate_changed:{previous_cache:.1%}->{current_cache:.1%}")
    if bool(current_backtest.get("cache_cold_start", False)) != bool(
        previous_backtest.get("cache_cold_start", False)
    ):
        reasons.append("cache_cold_start_state_changed")

    total = _metric(
        _number(current.get("elapsed_seconds")),
        _number(previous.get("elapsed_seconds")),
    )
    scan = _metric(
        _number(current_scan.get("total_seconds")),
        _number(previous_scan.get("total_seconds")),
    )
    backtest = _metric(
        _number(current_backtest.get("elapsed_seconds")),
        _number(previous_backtest.get("elapsed_seconds")),
    )

    comparable = not reasons
    status = "NONCOMPARABLE"
    if comparable:
        total_ratio = total["ratio"]
        scan_ratio = scan["ratio"]
        backtest_ratio = backtest["ratio"]
        regressions = [
            bool(total_ratio is not None and total_ratio >= _REGRESSION_RATIO and total["delta_seconds"] >= 10.0),
            bool(scan_ratio is not None and scan_ratio >= _REGRESSION_RATIO and scan["delta_seconds"] >= 5.0),
            bool(
                backtest_ratio is not None
                and backtest_ratio >= _REGRESSION_RATIO
                and backtest["delta_seconds"] >= 10.0
            ),
        ]
        improved = bool(
            total_ratio is not None
            and total_ratio <= _IMPROVEMENT_RATIO
            and total["delta_seconds"] <= -5.0
        )
        status = "REGRESSION" if any(regressions) else "IMPROVED" if improved else "STABLE"

    return {
        "version": PERFORMANCE_HEALTH_VERSION,
        "status": status,
        "comparable": comparable,
        "noncomparable_reasons": reasons,
        "universe_rows": {"current": current_rows, "previous": previous_rows},
        "cache_hit_rate": {"current": round(current_cache, 4), "previous": round(previous_cache, 4)},
        "total": total,
        "scan": scan,
        "backtest": backtest,
        "regression_threshold_ratio": _REGRESSION_RATIO,
        "improvement_threshold_ratio": _IMPROVEMENT_RATIO,
    }
