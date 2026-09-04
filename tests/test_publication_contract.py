from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from institution_scanner.publication_contract import (
    PUBLIC_CANDIDATE_COLUMNS,
    build_public_candidates,
    write_publication_contract,
)
from institution_scanner.publication_renderer import (
    PUBLIC_PAGE_VERSION,
    build_web_report,
)


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
    monkeypatch,
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
    cached_prices = pd.DataFrame(
        {"Close": [4.0, 4.2, 8.4]},
        index=pd.to_datetime(["2026-09-03", "2026-09-04", "2026-09-05"]),
    )
    monkeypatch.setattr(
        "institution_scanner.publication_renderer._load_cache",
        lambda _ticker: cached_prices,
    )

    result = build_web_report(output_dir=output, site_dir=site)

    page = result.index_path.read_text(encoding="utf-8")
    style_asset = f"report-{PUBLIC_PAGE_VERSION}.css"
    script_asset = f"report-{PUBLIC_PAGE_VERSION}.js"
    assert result.report_date == "2026-09-04"
    assert "先看风险与执行许可" in page
    assert "READY" in page
    assert "510300.SH" in page
    assert '<th title="截至报告日最近30个交易日收盘走势">TREND</th>' in page
    assert 'class="trend-chart trend-up"' in page
    assert 'aria-label="近2个交易日走势：上涨 +5.0%"' in page
    assert "Unused399" not in page
    assert "legacy-xxxx" not in page
    assert f'href="assets/{style_asset}"' in page
    assert f'src="assets/{script_asset}"' in page
    assert (site / "assets" / style_asset).is_file()
    assert (site / "assets" / script_asset).is_file()
    assert (site / "reports" / "index.html").is_file()


def test_production_publication_facades_do_not_import_versioned_renderers() -> None:
    root = Path(__file__).resolve().parents[1]
    versioned_import = re.compile(
        r"^(?:from|import)\s+web_report_v\d+",
        flags=re.MULTILINE,
    )
    for relative in (
        "web_report_v81.py",
        "institution_scanner/report_terminal.py",
    ):
        text = (root / relative).read_text(encoding="utf-8")
        assert not versioned_import.search(text), relative
        assert "import *" not in text, relative
