from __future__ import annotations

from institution_scanner.performance_health import build_performance_health


def _run(
    *,
    elapsed: float,
    scan: float,
    backtest: float,
    rows: int = 6800,
    cache: float = 0.9,
    mode: str = "FAST",
    cold: bool = False,
) -> dict[str, object]:
    return {
        "elapsed_seconds": elapsed,
        "requested_mode": mode,
        "universe": {"rows": rows},
        "scan_breakdown": {"total_seconds": scan},
        "backtest": {
            "elapsed_seconds": backtest,
            "cache_hit_rate": cache,
            "cache_cold_start": cold,
        },
    }


def test_comparable_runtime_regression_is_flagged() -> None:
    previous = _run(elapsed=300.0, scan=100.0, backtest=180.0)
    current = _run(elapsed=410.0, scan=135.0, backtest=250.0)

    health = build_performance_health(current, previous)

    assert health["comparable"] is True
    assert health["status"] == "REGRESSION"
    assert health["total"]["ratio"] == 1.3667


def test_cache_or_universe_migration_blocks_false_timing_attribution() -> None:
    previous = _run(elapsed=300.0, scan=100.0, backtest=180.0, rows=6800, cache=0.95)
    current = _run(elapsed=450.0, scan=140.0, backtest=280.0, rows=7200, cache=0.4)

    health = build_performance_health(current, previous)

    assert health["comparable"] is False
    assert health["status"] == "NONCOMPARABLE"
    reasons = health["noncomparable_reasons"]
    assert any(str(reason).startswith("universe_changed:") for reason in reasons)
    assert any(str(reason).startswith("cache_hit_rate_changed:") for reason in reasons)


def test_comparable_runtime_improvement_is_visible() -> None:
    previous = _run(elapsed=300.0, scan=100.0, backtest=180.0)
    current = _run(elapsed=240.0, scan=80.0, backtest=145.0)

    health = build_performance_health(current, previous)

    assert health["comparable"] is True
    assert health["status"] == "IMPROVED"
