from __future__ import annotations

"""Decision-focused Tkinter GUI entrypoint.

The implementation remains in ``gui_core.py``.  This thin presentation layer
keeps the first screen execution-focused while the detail dialog and exported
files retain the complete diagnostics.
"""

import re
import sys

import gui_core as _core

# Strict source semantics after provider-consistent cache hardening.
_core.DATA_SOURCE_HINTS.update(
    {
        "自动优选": "腾讯 / AKShare / 东方财富自动择优（统一前复权）",
        "AkShare": "仅使用 AkShare，不静默混源",
        "东方财富": "仅使用东方财富，不静默混源",
        "新浪财经": "仅使用新浪财经（独立缓存）",
        "腾讯财经": "仅使用腾讯财经，不静默混源",
    }
)

# Main table: trading decision first. Diagnostics remain in CSV/detail view.
_core.DISPLAY_COLUMNS = (
    "OverallRank",
    "Ticker",
    "Name",
    "Close",
    "EntrySignal",
    "EntryZone",
    "BreakoutBuyPrice",
    "StopLoss",
    "RankingEligibility",
    "RankingScore",
    "InstitutionalTier",
    "InstitutionalScore",
    "FinalScore",
    "QualityGate",
    "QualityDataCompleteness",
    "BacktestSamples",
    "BacktestConfidenceTier",
    "ValueTrapRisk",
    "ChaseRiskScore",
    "PassedFilters",
    "TradeReadinessReason",
    "DataAsOf",
    "RankingReason",
)

_core.COLUMN_NAMES.update(
    {
        "Close": "当日收盘价",
        "EntryZone": "回调买点",
        "BreakoutBuyPrice": "突破买点",
        "StopLoss": "止损位",
        "RankingEligibility": "交易资格",
        "TradeReadinessReason": "执行说明",
        "BreakoutScore": "突破强度",
        "InstitutionHoldingStatus": "机构覆盖趋势",
    }
)

_core.COLUMN_WIDTHS.update(
    {
        "OverallRank": 64,
        "Ticker": 90,
        "Name": 104,
        "Close": 86,
        "EntrySignal": 108,
        "EntryZone": 104,
        "BreakoutBuyPrice": 88,
        "StopLoss": 72,
        "RankingEligibility": 74,
        "RankingScore": 82,
        "InstitutionalTier": 92,
        "InstitutionalScore": 78,
        "FinalScore": 76,
    }
)


def _update_market_overview_decision(self, rows, indexes) -> None:
    if not hasattr(self, "market_overview"):
        return
    total, _active, _confirmed, breakout, actionable, average = (
        self._market_overview_values(rows, indexes)
    )
    self.market_overview.set(
        f"概览：{total} 只 · 启动 {breakout} · 可交易 {actionable} · 最终均分 {average:.1f}"
    )


_core.ScannerGUI._update_market_overview = _update_market_overview_decision

_original_render_cached_rows = _core.ScannerGUI._render_cached_rows


def _render_cached_rows_decision(self) -> bool:
    rendered = _original_render_cached_rows(self)
    if rendered and hasattr(self, "result_summary"):
        summary = self.result_summary.get()
        summary = re.sub(r" · 过期 \d+", "", summary)
        self.result_summary.set(summary)
    return rendered


_core.ScannerGUI._render_cached_rows = _render_cached_rows_decision

# Preserve the historical import surface used by tests and external launchers.
if __name__ == "__main__":
    _core.main()
else:
    sys.modules[__name__] = _core
