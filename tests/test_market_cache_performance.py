from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pandas as pd

from institution_scanner import market_cache_performance as performance


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [1.0],
            "High": [1.0],
            "Low": [1.0],
            "Close": [1.0],
            "Volume": [100.0],
        }
    )


def test_batch_writes_finish_before_manifest(monkeypatch, tmp_path) -> None:
    events: list[tuple[str, str]] = []
    core = SimpleNamespace()
    core.logger = logging.getLogger("market-cache-test")
    core.normalize_ticker = lambda value: str(value).upper()
    core._cache_path = lambda ticker, source=None: tmp_path / f"{ticker}.parquet"

    def validate(frame: pd.DataFrame) -> pd.DataFrame:
        events.append(("validate", "frame"))
        return frame.copy()

    core._validate_ohlcv = validate
    core._save_cache = lambda *args, **kwargs: events.append(("legacy-save", str(args[0])))
    core._record_market_manifest = lambda ticker, frame: events.append(
        ("manifest", str(ticker))
    )
    core._flush_market_manifest = lambda: events.append(("flush", "manifest"))

    def batch(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        for ticker, frame in frames.items():
            core._save_cache(ticker, frame)
        for ticker, frame in frames.items():
            core._record_market_manifest(ticker, frame)
        core._flush_market_manifest()
        return frames

    core.download_batch = batch
    performance.install(core)

    def write(core_arg: Any, ticker: str, frame: pd.DataFrame, source: str | None) -> None:
        del core_arg, frame, source
        events.append(("write", ticker))

    monkeypatch.setattr(performance, "_write_validated_frame", write)
    frames = {ticker: _frame() for ticker in ("B.ST", "A.ST", "C.ST")}

    result = core.download_batch(frames)

    assert result is frames
    assert {value for kind, value in events if kind == "write"} == set(frames)
    first_manifest = next(index for index, event in enumerate(events) if event[0] == "manifest")
    assert all(
        index < first_manifest
        for index, event in enumerate(events)
        if event[0] == "write"
    )
    assert events[-1] == ("flush", "manifest")
    assert not any(kind == "legacy-save" for kind, _ in events)


def test_validated_frame_skips_second_validation(monkeypatch, tmp_path) -> None:
    calls = {"validate": 0, "write": 0}
    core = SimpleNamespace()
    core.logger = logging.getLogger("market-cache-test")
    core.normalize_ticker = lambda value: str(value).upper()
    core._cache_path = lambda ticker, source=None: tmp_path / f"{ticker}.parquet"

    def validate(frame: pd.DataFrame) -> pd.DataFrame:
        calls["validate"] += 1
        return frame.copy()

    core._validate_ohlcv = validate
    core._save_cache = lambda *args, **kwargs: None
    core._record_market_manifest = lambda ticker, frame: None
    core._flush_market_manifest = lambda: None
    core.download_batch = lambda *args, **kwargs: {}
    performance.install(core)

    def write(core_arg: Any, ticker: str, frame: pd.DataFrame, source: str | None) -> None:
        del core_arg, ticker, frame, source
        calls["write"] += 1

    monkeypatch.setattr(performance, "_write_validated_frame", write)
    frame = _frame()
    frame.attrs[performance.VALIDATED_FRAME_ATTR] = True

    core._save_cache("A.ST", frame)

    assert calls == {"validate": 0, "write": 1}
