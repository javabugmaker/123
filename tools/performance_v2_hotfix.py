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


# Preserve the legacy cmd_backtest mock contract: auto is the function default,
# so only pass an explicit mode keyword when the caller actually chose fast/exact.
main = read("main.py")
main = replace_once(
    main,
    '''    summary = run_historical_backtest(
        unique_tickers,
        source=args.data_source,
        workers=getattr(args, "workers", None),
        mode=requested_mode,
        **options,
    )
''',
    '''    backtest_kwargs = {
        "source": args.data_source,
        "workers": getattr(args, "workers", None),
        **options,
    }
    if requested_mode != "auto":
        backtest_kwargs["mode"] = requested_mode
    summary = run_historical_backtest(unique_tickers, **backtest_kwargs)
''',
    "main auto mode compatibility",
)
write("main.py", main)


# Preserve the direct _backtest_one_ticker compatibility hook used by tests and
# third-party callers.  Real cache-miss behavior is unchanged because the real
# function still returns [] when no valid market frame exists.
analytics = read("analytics.py")
analytics = replace_once(
    analytics,
    '''    frame = _load_cache(ticker, source)
    if frame is None or len(frame) < 300:
        return [], False
    active_profile = profile or _resolve_backtest_profile("exact", 1)
''',
    '''    frame = _load_cache(ticker, source)
    if frame is None or len(frame) < 300:
        return (
            _backtest_one_ticker(
                ticker,
                source,
                benchmark_frame,
                commission,
                stamp_duty,
                slippage,
                split_dates,
            ),
            False,
        )
    active_profile = profile or _resolve_backtest_profile("exact", 1)
''',
    "backtest direct compatibility",
)
write("analytics.py", analytics)


# The incremental indicator test must be longer than the configured 620-bar
# recomputation tail; otherwise a correct bounded-tail recompute equals the full
# tiny fixture length and cannot demonstrate the optimization.
tests = read("test_performance_regressions.py")
tests = replace_once(
    tests,
    '''        base_index = pd.date_range("2026-01-01", periods=260, freq="B")
        base = pd.DataFrame(
            {
                "Open": range(260),
                "High": [value + 2 for value in range(260)],
                "Low": [max(0, value - 1) for value in range(260)],
                "Close": [value + 1 for value in range(260)],
                "Volume": [1000 + value for value in range(260)],
''',
    '''        base_index = pd.date_range("2023-01-02", periods=800, freq="B")
        base = pd.DataFrame(
            {
                "Open": range(800),
                "High": [value + 2 for value in range(800)],
                "Low": [max(0, value - 1) for value in range(800)],
                "Close": [value + 1 for value in range(800)],
                "Volume": [1000 + value for value in range(800)],
''',
    "incremental test base length",
)
tests = replace_once(
    tests,
    '''                    {"Open": [260.0], "High": [262.0], "Low": [259.0], "Close": [261.0], "Volume": [1260.0]},
''',
    '''                    {"Open": [800.0], "High": [802.0], "Low": [799.0], "Close": [801.0], "Volume": [1800.0]},
''',
    "incremental test appended bar",
)
write("test_performance_regressions.py", tests)

print("Performance v2 regression hotfix applied.")
