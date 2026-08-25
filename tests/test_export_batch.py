from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

from institution_scanner.export_batch import install


def test_canonical_backtest_defers_intermediate_exports_and_materializes_once(
    tmp_path: Path,
) -> None:
    refresh_calls: list[tuple[int, object]] = []
    resonance_flags: list[bool] = []

    def refresh(
        frame: pd.DataFrame,
        *,
        output_dir: object = None,
        **kwargs: Any,
    ) -> None:
        del kwargs
        refresh_calls.append((len(frame), output_dir))

    report = SimpleNamespace(refresh_candidate_exports=refresh)

    def legacy(_args: object) -> int:
        frame = pd.DataFrame({"Ticker": ["A.ST", "B.ST"]})
        report.refresh_candidate_exports(frame, output_dir=tmp_path)
        report.refresh_candidate_exports(frame, output_dir=tmp_path)
        report.refresh_candidate_exports(frame, output_dir=tmp_path)
        return 0

    def resonance(
        stage: object,
        *,
        refresh_candidate_exports: bool = True,
    ) -> dict[str, object]:
        del stage
        resonance_flags.append(bool(refresh_candidate_exports))
        return {
            "status": "MATERIALIZED",
            "ticker_metrics": 2,
            "diagnostic_groups": 1,
        }

    module = SimpleNamespace(
        _LEGACY_CMD_BACKTEST=legacy,
        _materialize_resonance_stage=lambda stage: None,
        _canonical_backtest_runtime=lambda: True,
        _report=report,
        materialize_resonance_outputs=resonance,
        logger=logging.getLogger("test.export_batch"),
    )

    install(module)

    assert module._LEGACY_CMD_BACKTEST(None) == 0
    assert refresh_calls == []

    pd.DataFrame(
        {"Ticker": ["A.ST", "B.ST"], "FinalScore": [60.0, 55.0]}
    ).to_csv(tmp_path / "AllResults.csv", index=False, encoding="utf-8-sig")

    module._materialize_resonance_stage(tmp_path)

    assert resonance_flags == [False]
    assert refresh_calls == [(2, tmp_path)]


def test_noncanonical_runtime_keeps_legacy_refresh_behavior(
    tmp_path: Path,
) -> None:
    del tmp_path
    calls: list[int] = []

    def refresh(
        frame: pd.DataFrame,
        *,
        output_dir: object = None,
        **kwargs: Any,
    ) -> None:
        del output_dir, kwargs
        calls.append(len(frame))

    report = SimpleNamespace(refresh_candidate_exports=refresh)

    def legacy(_args: object) -> int:
        report.refresh_candidate_exports(pd.DataFrame({"Ticker": ["A.ST"]}))
        return 0

    module = SimpleNamespace(
        _LEGACY_CMD_BACKTEST=legacy,
        _materialize_resonance_stage=lambda stage: None,
        _canonical_backtest_runtime=lambda: False,
        _report=report,
        materialize_resonance_outputs=lambda *args, **kwargs: {},
        logger=logging.getLogger("test.export_batch.noncanonical"),
    )

    install(module)

    assert module._LEGACY_CMD_BACKTEST(None) == 0
    assert calls == [1]
