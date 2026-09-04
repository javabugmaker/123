from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from institution_scanner.publication_contract import (
    PUBLIC_CANDIDATE_COLUMNS,
    build_public_candidates,
    write_publication_contract,
)
from institution_scanner.publication_renderer import build_web_report


def _wide_candidates() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Ticker": ["510300.SH", "600001.SH"],
            "Name": ["沪深300ETF", "测试股份"],
            "AssetType": ["etf", "stock"],
            "IsETF": [True, False],
            "Industry": ["宽基", "制造"],
            "Close": [4.2, 10.0],
            "FinalScore": [67.0, 60.0],
            "ExecutionState": ["READY", "OBSERVE"],
            "EntrySignal": ["BREAKOUT_CONFIRM", "WAIT_PULLBACK"],
            "DataAsOf": ["2026-09-04", "2026-09-04"],
            "DataFreshnessStatus": ["新鲜", "新鲜"],
            "StopLoss": [4.0, 9.2],
            "ProjectedTarget": [4.7, 11.4],
            "RunId": ["run-1", "run-1"],
            "ModelVersion": ["legacy-" + "x" * 2_000] * 2,
            "PipelineVersion": ["legacy-" + "y" * 2_000] * 2,
            **{f"Unused{index}": [index, index] for index in range(400)},
        }
    )


def test_public_candidates_are_narrow_and_do_not_repeat_legacy_versions() -> None:
    source = _wide_candidates()
    trade_ready = source.iloc[[0]].copy()

    compact = build_public_candidates(
        source,
        views={"MIXED_RESEARCH": source, "TRADE_READY": trade_ready},
    )

    assert len(compact.columns) == len(PUBLIC_CANDIDATE_COLUMNS) + 9
    assert "ModelVersion" not in compact.columns
    assert "PipelineVersion" not in compact.columns
    assert not any(column.startswith("Unused") for column in compact.columns)
    assert compact["AlphaScore"].tolist() == [67.0, 60.0]
    assert compact["InTradeReady"].tolist() == [True, False]


def test_renderer_consumes_compact_contract_and_externalizes_assets(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    site = tmp_path / "site"
    output.mkdir()
    source = _wide_candidates()
    write_publication_contract(
        source,
        destination=output,
        source_rows=source,
        views={"MIXED_RESEARCH": source, "TRADE_READY": source.iloc[[0]]},
    )
    (output / "DailyRunSummary.json").write_text(
        json.dumps({"universe": {"rows": 6_824}}),
        encoding="utf-8",
    )

    result = build_web_report(output_dir=output, site_dir=site)

    page = result.index_path.read_text(encoding="utf-8")
    assert result.report_date == "2026-09-04"
    assert "先看风险与执行许可" in page
    assert "READY" in page
    assert "510300.SH" in page
    assert "Unused399" not in page
    assert "legacy-xxxx" not in page
    assert 'href="assets/report-v114.css"' in page
    assert 'src="assets/report-v114.js"' in page
    assert (site / "assets" / "report-v114.css").is_file()
    assert (site / "assets" / "report-v114.js").is_file()
    assert (site / "reports" / "index.html").is_file()
