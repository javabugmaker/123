"""Behavioural coverage for the live *chain* around ``scan_single_from_df``.

Companion to ``tests/test_scanner_core_behaviour.py`` (which tests the
convergence point itself).  This file covers the three functions that feed it
and the two helpers the resume path calls into:

=================================================  =====  ==========================
symbol                                             lines  caller
=================================================  =====  ==========================
``_analyse_one_ticker_from_df``                    34     ``v59.run_scan:564`` (daily)
``_analyse_one_ticker``                            13     ``main_core:188`` (indicator)
``run_parallel_indicator_scan``                    32     ``main_core:188``
``_emit_progress`` / ``_raise_if_cancelled``       21     ``scanner_resume_v59`` (13 call sites)
=================================================  =====  ==========================

Why these and not the rest
--------------------------
``tests/test_scanner_core_provenance.py`` established that ``run_scan``'s 828
lines are *not* on either production path -- they are reached only through a
checkpoint-compatibility branch.  Everything tested here is reached on every
run.  ``_raise_if_cancelled`` is included because the provenance pass listed
only ``_emit_progress`` as "live but thin": ``scanner_resume_v59`` also calls
``_raise_if_cancelled`` at lines 388, 476, 583, 655 and 675, so it is live too.

Isolation
---------
``load_or_compute_indicators`` and ``scan_single_from_df`` are stubbed wherever
the assertion is about *this* function's own branching.  Nothing here reads
``cache/``: ``_cache_path_for`` is only asked for a path, never opened, and the
indicator cache is bypassed by the stub.
"""

from __future__ import annotations

import threading

import numpy as np
import pandas as pd
import pytest

import scanner_core as scanner

# The whitelist ``_analyse_one_ticker_from_df`` slices down to (scanner_core:1721).
_ENRICHMENT_COLUMNS = [
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    "MA20",
    "MA50",
    "ATR14",
    "ATR50",
    "RSI14",
]


def _ticker(ticker: str = "600000.SH", **kwargs: object) -> scanner.TickerInfo:
    return scanner.TickerInfo(ticker=ticker, name=ticker, **kwargs)  # type: ignore[arg-type]


def _frame(columns: list[str] | None = None) -> pd.DataFrame:
    """Small OHLCV frame; only its *shape* matters here, not its values."""
    columns = columns if columns is not None else ["Open", "High", "Low", "Close", "Volume"]
    index = pd.bdate_range("2023-01-02", periods=40)
    data = {column: np.linspace(1.0, 2.0, len(index)) for column in columns}
    return pd.DataFrame(data, index=index)


# ---------------------------------------------------------------------------
# _analyse_one_ticker_from_df
# ---------------------------------------------------------------------------


def test_a_missing_frame_short_circuits_before_enrichment(monkeypatch) -> None:
    """``df is None`` must not reach the indicator cache path.

    The stub raises, so reaching ``load_or_compute_indicators`` would fail the
    test rather than silently passing.  Degenerating to ``scan_single_from_df``
    is what produces the ``Insufficient data`` result the daily path relies on.
    """
    monkeypatch.setattr(
        scanner,
        "load_or_compute_indicators",
        lambda *args, **kwargs: pytest.fail("enrichment ran for a missing frame"),
    )
    result, enriched = scanner._analyse_one_ticker_from_df(_ticker(), None)
    assert result.error == "Insufficient data"
    assert enriched is None


def test_a_real_frame_is_forwarded_with_indicators_computed(monkeypatch) -> None:
    """The flag is the whole point of this wrapper (scanner_core:1718).

    ``scan_single_from_df`` recomputes indicators when it is False, so a
    regression here would silently double the indicator cost of every daily run
    rather than change any number.
    """
    seen: dict[str, object] = {}

    def fake_scan(ticker_info, df, indicators_computed=False):
        seen["indicators_computed"] = indicators_computed
        seen["frame_is_enriched"] = "MA20" in df.columns
        return scanner.ScanResult(ticker=ticker_info.ticker, name=ticker_info.name)

    def fake_enrich(ticker, df, compute_fn, source_path=None, enabled=True):
        enriched = df.copy()
        enriched["MA20"] = 1.0
        return enriched, False

    monkeypatch.setattr(scanner, "scan_single_from_df", fake_scan)
    monkeypatch.setattr(scanner, "load_or_compute_indicators", fake_enrich)
    scanner._analyse_one_ticker_from_df(_ticker(), _frame())

    assert seen["indicators_computed"] is True
    assert seen["frame_is_enriched"] is True


def test_a_failed_scan_discards_the_enriched_frame(monkeypatch) -> None:
    """Error results carry no frame (scanner_core:1719-1720).

    Worth pinning because the caller treats a non-None second element as
    "this ticker is publishable"; leaking a frame for a failed scan would put
    an unscored row into the enriched output.
    """
    monkeypatch.setattr(
        scanner,
        "scan_single_from_df",
        lambda ticker_info, df, indicators_computed=False: scanner.ScanResult(
            ticker=ticker_info.ticker, error="boom"
        ),
    )
    monkeypatch.setattr(
        scanner,
        "load_or_compute_indicators",
        lambda *args, **kwargs: (_frame(), False),
    )
    result, enriched = scanner._analyse_one_ticker_from_df(_ticker(), _frame())
    assert result.error == "boom"
    assert enriched is None


def test_the_returned_frame_is_a_whitelisted_copy(monkeypatch) -> None:
    """Two contracts in one: column whitelist, and no shared memory.

    The whitelist (scanner_core:1721-1735) is what keeps indicator internals out
    of the enriched output.  ``.copy()`` matters because the frame handed back
    outlives this call -- a view onto the enrichment buffer would let a
    downstream write corrupt the next ticker's read.
    """
    wide = _frame(["Open", "High", "Low", "Close", "Volume", "MA20", "SECRET_INTERNAL"])

    monkeypatch.setattr(
        scanner,
        "scan_single_from_df",
        lambda ticker_info, df, indicators_computed=False: scanner.ScanResult(
            ticker=ticker_info.ticker
        ),
    )
    monkeypatch.setattr(scanner, "load_or_compute_indicators", lambda *a, **k: (wide, False))

    _result, enriched = scanner._analyse_one_ticker_from_df(_ticker(), _frame())

    assert "SECRET_INTERNAL" not in enriched.columns, "a non-whitelisted column leaked"
    assert set(enriched.columns) <= set(_ENRICHMENT_COLUMNS)
    assert "MA20" in enriched.columns, "a whitelisted column was dropped"
    assert not np.shares_memory(
        enriched.to_numpy(), wide[enriched.columns].to_numpy()
    ), "the enriched frame aliases the caller's buffer"


# ---------------------------------------------------------------------------
# _analyse_one_ticker
# ---------------------------------------------------------------------------


def test_a_cache_miss_reports_the_ticker_exactly_as_given(monkeypatch) -> None:
    """The miss branch (scanner_core:1743-1750) does **not** normalise.

    This is a real fork between the two branches of ``_analyse_one_ticker``,
    found by writing the test -- it is pinned as-is, not "fixed":

    * miss  -> ``ScanResult(ticker=ticker_info.ticker)``, verbatim
    * hit   -> ``scan_single_from_df`` normalises **and writes it back** onto
      the caller's ``TickerInfo`` (scanner_core:454-455)

    So a lower-case or padded ticker stays lower-case in the miss result and
    comes back upper-case in the hit result.  It is not a lookup bug:
    ``_load_cache`` -> ``_cache_path`` -> ``_safe_cache_stem`` normalises before
    touching disk, so both branches find the same file.  The divergence is
    confined to the string that lands in the published result.

    Visible only when a caller passes an un-normalised ticker.  Left alone
    deliberately: changing it would alter published output, which is a call for
    the maintainer, not for a test.
    """
    monkeypatch.setattr(scanner, "_load_cache", lambda *args, **kwargs: None)
    result = scanner._analyse_one_ticker(_ticker("600000.sh"), data_source="akshare")

    assert result.error.startswith("No cached data")
    assert result.ticker == "600000.sh"
    assert result.is_etf is False


def test_a_cache_miss_carries_the_etf_flag(monkeypatch) -> None:
    """``is_etf`` is one of only three fields this branch propagates."""
    monkeypatch.setattr(scanner, "_load_cache", lambda *args, **kwargs: None)
    result = scanner._analyse_one_ticker(_ticker(is_etf=True))
    assert result.error.startswith("No cached data")
    assert result.is_etf is True


def test_a_cache_hit_returns_only_the_scan_result(monkeypatch) -> None:
    """The wrapper must unwrap the ``(result, frame)`` pair (scanner_core:1751).

    Returning the tuple would make every callers' ``result.score`` an
    AttributeError rather than a wrong number, so this is cheap insurance.
    """
    expected = scanner.ScanResult(ticker="600000.SH", name="600000.SH")
    monkeypatch.setattr(scanner, "_load_cache", lambda *args, **kwargs: _frame())
    monkeypatch.setattr(
        scanner,
        "_analyse_one_ticker_from_df",
        lambda *args, **kwargs: (expected, _frame()),
    )
    assert scanner._analyse_one_ticker(_ticker()) is expected


# ---------------------------------------------------------------------------
# run_parallel_indicator_scan
# ---------------------------------------------------------------------------


def test_a_legacy_data_source_is_normalised_before_dispatch(monkeypatch) -> None:
    """``run_parallel_indicator_scan`` normalises once, up front (scanner_core:1759).

    Legacy names are read-only migration aliases; the worker must never see the
    raw string, or downstream cache paths would fork per historical alias.
    """
    seen: list[str] = []

    def fake_analyse(ticker_info, data_source="tickflow"):
        seen.append(data_source)
        return scanner.ScanResult(ticker=ticker_info.ticker)

    monkeypatch.setattr(scanner, "_analyse_one_ticker", fake_analyse)
    scanner.run_parallel_indicator_scan([_ticker()], data_source="akshare")
    assert seen == ["tickflow"]


def test_an_unknown_data_source_is_rejected_before_any_work(monkeypatch) -> None:
    """Fail fast, and fail empty -- not after dispatching to a thread pool."""
    monkeypatch.setattr(
        scanner,
        "_analyse_one_ticker",
        lambda *args, **kwargs: pytest.fail("worker ran despite an invalid source"),
    )
    with pytest.raises(ValueError, match="行情数据源"):
        scanner.run_parallel_indicator_scan([_ticker()], data_source="bloomberg")


@pytest.mark.parametrize("max_workers", [0, -3])
def test_a_non_positive_worker_count_is_clamped(max_workers: int, monkeypatch) -> None:
    """``max(1, int(max_workers))`` at scanner_core:1761.

    A zero-worker ThreadPoolExecutor raises at construction, so an unclamped
    value would take down the whole indicator run rather than degrade it.
    """
    monkeypatch.setattr(
        scanner,
        "_analyse_one_ticker",
        lambda ticker_info, data_source="tickflow": scanner.ScanResult(
            ticker=ticker_info.ticker
        ),
    )
    results = scanner.run_parallel_indicator_scan([_ticker()], max_workers=max_workers)
    assert len(results) == 1


def test_an_empty_batch_returns_empty(monkeypatch) -> None:
    """No pool, no progress bar, no exception."""
    monkeypatch.setattr(
        scanner,
        "_analyse_one_ticker",
        lambda *args, **kwargs: pytest.fail("worker ran for an empty batch"),
    )
    assert scanner.run_parallel_indicator_scan([]) == []


# ---------------------------------------------------------------------------
# progress / cancellation helpers (live via scanner_resume_v59)
# ---------------------------------------------------------------------------


def test_a_failing_progress_callback_never_escapes() -> None:
    """Swallowed by design (scanner_core:96-99).

    A progress callback is instrumentation; a GUI or logger error there must not
    abort a scan that is otherwise healthy.
    """

    def exploding_callback(stage, current, total, message):
        raise RuntimeError("gui went away")

    scanner._emit_progress(exploding_callback, "scan", 1, 5, "whatever")


def test_a_missing_progress_callback_is_a_no_op() -> None:
    scanner._emit_progress(None, "scan", 1, 5, "whatever")


def test_progress_arguments_are_coerced_to_int_and_str() -> None:
    """``int(current)`` / ``str(message)`` at scanner_core:97.

    Callers pass numpy ints and non-string messages; the coercion is what keeps
    the published progress contract stable across them.
    """
    seen: list[tuple] = []
    scanner._emit_progress(
        lambda stage, current, total, message: seen.append((stage, current, total, message)),
        "scan",
        np.int64(3),
        5.0,
        123,
    )
    stage, current, total, message = seen[0]
    assert (stage, current, total, message) == ("scan", 3, 5, "123")
    assert isinstance(current, int) and isinstance(total, int) and isinstance(message, str)


def test_cancellation_is_only_raised_when_the_event_is_set() -> None:
    """Cooperative cancellation; ``None`` means "never cancel"."""
    scanner._raise_if_cancelled(None)
    scanner._raise_if_cancelled(threading.Event())

    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(scanner.ScanCancelled):
        scanner._raise_if_cancelled(cancelled)


def test_scan_cancelled_is_a_runtime_error() -> None:
    """Callers catch ``RuntimeError`` broadly around the scan loop."""
    assert issubclass(scanner.ScanCancelled, RuntimeError)
