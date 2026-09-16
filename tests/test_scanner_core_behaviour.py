"""Behavioural coverage for the part of ``scanner_core`` production runs.

Scope, and one deliberate omission
----------------------------------
``tests/test_scanner_core_provenance.py`` established that only about a quarter
of ``scanner_core`` is alive: ``scan_single_from_df`` is the convergence point
of both production paths, and ``run_scan`` (828 lines) is not on either of them.
This file tests the live part.

**It pins no numeric scores, on purpose.** ``scan_single_from_df`` calls
``score_ticker``, ``classify_style``, ``entry_point`` and ``smart_money_stage``,
and §10.2 established that in production those resolve to overlays
(``score`` v113 facade, ``score_acceleration_v79``, ``score_cache_guard_v80``),
with 0 of 10 core scoring functions still defined in ``score_core``.  A bare
test process therefore scores with the *source* implementations, not the
shipped ones.  Any assertion of the form "this frame scores 28.9179" would be
freezing a number production never produces -- and would go red the moment an
overlay legitimately changes its maths.

What is asserted instead are the properties that must hold whichever scoring
implementation is installed: short-circuit branches, determinism, field ranges
and completeness, ordering, and failure isolation.  Those are the parts that
would silently break and that nobody currently checks.

The frame
---------
260 business days of synthetic OHLCV with a fixed seed.  Synthetic rather than
a cached real frame so the test does not depend on ``cache/`` (untracked) or on
market history staying available.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import scanner_core as scanner

BARS = 260


def _frame(*, bars: int = BARS, last_close: float | None = None) -> pd.DataFrame:
    """Deterministic OHLCV, long enough for every indicator to be defined."""
    rng = np.random.default_rng(20260916)
    index = pd.bdate_range("2023-01-02", periods=bars)
    close = np.abs(10.0 + np.cumsum(rng.normal(0.0, 0.12, bars))) + 1.0
    if last_close is not None:
        close = close.copy()
        close[-1] = last_close
    return pd.DataFrame(
        {
            "Open": close * (1.0 + rng.normal(0.0, 0.004, bars)),
            "High": close * (1.0 + np.abs(rng.normal(0.0, 0.008, bars))),
            "Low": close * (1.0 - np.abs(rng.normal(0.0, 0.008, bars))),
            "Close": close,
            "Volume": rng.integers(100_000, 900_000, bars).astype(float),
        },
        index=index,
    )


def _ticker(
    ticker: str = "000001.SZ",
    name: str = "测试标的",
    **kwargs: object,
) -> scanner.TickerInfo:
    return scanner.TickerInfo(ticker=ticker, name=name, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "frame",
    [None, pd.DataFrame(), _frame(bars=19)],
    ids=["none", "empty", "too_short"],
)
def test_frames_without_enough_history_short_circuit(frame: pd.DataFrame | None) -> None:
    """The guard before any indicator work, and the identity it keeps.

    Three ways to be too short, all of which must produce a result that still
    carries the ticker's identity -- callers merge on ``ticker``, so a result
    that lost it would corrupt downstream output rather than merely be empty.
    """
    info = _ticker(name="平安银行", sector="银行", industry="银行")
    result = scanner.scan_single_from_df(info, frame)
    assert result.error == "Insufficient data"
    assert result.ticker == "000001.SZ"
    assert result.name == "平安银行"
    assert result.sector == "银行"
    assert result.industry == "银行"


def test_a_non_finite_last_close_short_circuits_before_scoring() -> None:
    """A NaN close must not be scored into a number.

    The frame is long enough to pass the history guard, so this isolates the
    second guard -- otherwise the first one would make the assertion vacuous.
    """
    result = scanner.scan_single_from_df(_ticker(), _frame(last_close=np.nan))
    assert result.error == "最新收盘价无效"
    assert result.score.total == 0.0


def test_the_ticker_is_normalised_in_place() -> None:
    """The function mutates its argument; callers rely on that.

    ``scan_single_from_df`` writes the normalised ticker back onto the
    ``TickerInfo`` it was handed (``scanner_core:455-456``).  That is a real
    side effect on shared input, so it is pinned rather than left implicit.
    """
    info = _ticker("000001.sz ")
    result = scanner.scan_single_from_df(info, _frame())
    assert info.ticker == "000001.SZ", "the caller's TickerInfo was not normalised"
    assert result.ticker == info.ticker


def test_the_same_frame_scores_identically_twice() -> None:
    """Determinism, which nothing currently checks.

    Not a tautology: the scan reads caches, theme tables and market-cap lookups
    that could legitimately vary between runs.  If any of that leaks in, the
    published ranking stops being reproducible.
    """
    first = scanner.scan_single_from_df(_ticker(), _frame())
    second = scanner.scan_single_from_df(_ticker(), _frame())
    assert first.error == "" and second.error == ""
    assert first.score == second.score


def test_a_full_scan_populates_a_complete_breakdown() -> None:
    """Shape and range, not values.

    Every component finite, no missing indicators, coverage full, and the
    headline number inside [0, 100].  These hold for any correct scoring
    implementation, which is exactly why they are safe to pin here.
    """
    result = scanner.scan_single_from_df(_ticker(), _frame())
    assert result.error == ""
    assert result.score.missing_indicators == 0
    assert result.score.indicator_coverage == 1.0
    assert 0.0 <= result.score.total <= 100.0

    breakdown = result.score
    for name in ("trend", "volume", "accumulation", "volatility", "structure"):
        value = getattr(breakdown, name)
        assert np.isfinite(value), f"{name} is not finite: {value}"
        assert value >= 0.0, f"{name} is negative: {value}"
    assert breakdown.contributions, "no contribution breakdown was recorded"


def test_a_ticker_with_no_cached_data_is_reported_not_raised(monkeypatch) -> None:
    """The cache-miss branch of the per-ticker helper.

    ``_load_cache`` is stubbed to return nothing so this never touches disk or
    network; the point is that a missing frame yields an error result rather
    than an exception that would kill a batch.
    """
    monkeypatch.setattr(scanner, "_load_cache", lambda *args, **kwargs: None)
    result = scanner._analyse_one_ticker(_ticker("999999.XX"))
    assert result.error.startswith("No cached data")
    assert result.ticker == "999999.XX"


def test_parallel_scan_orders_results_by_score_descending(monkeypatch) -> None:
    """The ordering contract of the indicator path (``main_core:188``).

    ``_analyse_one_ticker`` is stubbed with fixed scores because ordering is
    this function's own responsibility; stubbing also keeps the assertion
    independent of which scoring overlay is installed.
    """
    totals = {"A": 10.0, "B": 50.0, "C": 30.0, "D": 70.0}

    def fake_analyse(info, data_source="tickflow"):
        result = scanner.ScanResult(ticker=info.ticker, name=info.name)
        result.score = scanner.ScoreBreakdown(total=totals[info.ticker])
        return result

    monkeypatch.setattr(scanner, "_analyse_one_ticker", fake_analyse)
    results = scanner.run_parallel_indicator_scan(
        [_ticker(ticker=key, name=key) for key in totals]
    )
    assert [item.ticker for item in results] == ["D", "B", "C", "A"]


def test_parallel_scan_isolates_a_failing_ticker(monkeypatch) -> None:
    """One bad ticker must not cost the whole batch.

    The worker loop catches per-ticker exceptions and records them as error
    results (``scanner_core:1776-1785``); a regression there would drop every
    result for one bad frame.
    """
    def fake_analyse(info, data_source="tickflow"):
        if info.ticker == "BAD":
            raise RuntimeError("indicator blew up")
        result = scanner.ScanResult(ticker=info.ticker, name=info.name)
        result.score = scanner.ScoreBreakdown(total=40.0)
        return result

    monkeypatch.setattr(scanner, "_analyse_one_ticker", fake_analyse)
    results = scanner.run_parallel_indicator_scan(
        [_ticker("OK1"), _ticker("BAD"), _ticker("OK2")]
    )
    assert len(results) == 3, "a failing ticker dropped results from the batch"
    failed = [item for item in results if item.error]
    assert len(failed) == 1
    assert failed[0].ticker == "BAD"
    assert "indicator blew up" in failed[0].error
