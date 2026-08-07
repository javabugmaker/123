from __future__ import annotations

"""Decision-focused Tkinter GUI entrypoint.

The stable implementation remains in ``gui_core.py``.  This entry module only
changes presentation defaults, so ranking, freshness checks, hard-risk logic,
row colouring, exports and the full detail dialog continue to use the existing
engine unchanged.
"""

import re
import sys

import gui_core as _core

# ---------------------------------------------------------------------------
# Main table: trading decision first
# ---------------------------------------------------------------------------
# Diagnostic fields such as MarketRegime / DataFreshnessStatus / HardRiskFlag
# are intentionally not part of the first-screen grid.  They are still kept in
# the CSV, ranking/risk pipeline and the double-click detail dialog.
_core.DISPLAY_COLUMNS = (
    "OverallRank",
    "Ticker",
    "Name",
    "AssetType",
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
        "EntryZone": "回调买点",
        "BreakoutBuyPrice": "突破买点",
        "StopLoss": "止损位",
        "RankingEligibility": "交易资格",
        "TradeReadinessReason": "执行说明",
    }
)

# Keep the most useful decision fields visible within a normal 1440px window.
_core.COLUMN_WIDTHS.update(
    {
        "OverallRank": 64,
        "Ticker": 90,
        "Name": 104,
        "AssetType": 54,
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

# ---------------------------------------------------------------------------
# Compact overview: remove MarketRegime from the visible GUI strip.
# The market-regime calculation itself is deliberately retained in the engine.
# ---------------------------------------------------------------------------
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

# The old render summary appended "过期 N".  Keep freshness enforcement and
# row risk colouring, but remove that diagnostic counter from the visible bar.
_original_render_cached_rows = _core.ScannerGUI._render_cached_rows


def _render_cached_rows_decision(self) -> bool:
    rendered = _original_render_cached_rows(self)
    if rendered and hasattr(self, "result_summary"):
        summary = self.result_summary.get()
        summary = re.sub(r" · 过期 \d+", "", summary)
        self.result_summary.set(summary)
    return rendered


_core.ScannerGUI._render_cached_rows = _render_cached_rows_decision

# Make ``import gui`` resolve to the real implementation module after applying
# the presentation patches.  This keeps existing tests/patches such as
# patch("gui.OUTPUT_DIR") working exactly as before.
if __name__ == "__main__":
    _core.main()
else:
    sys.modules[__name__] = _core
