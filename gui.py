from __future__ import annotations

"""Tkinter GUI entrypoint.

The implementation lives in ``gui_core.py``.  This thin layer keeps the main
screen decision-focused without deleting the underlying risk/freshness logic
used by ranking, exports, row colouring, and the detail dialog.
"""

import gui_core as _core
from gui_core import *  # noqa: F401,F403 - preserve the existing public GUI API

# Main result grid: put actionable price levels back next to the signal and
# keep diagnostic-only fields out of the first screen.  The removed fields are
# still available in row details and still participate in ranking/risk logic.
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

_core.COLUMN_NAMES["EntryZone"] = "回调买点"
_core.COLUMN_NAMES["BreakoutBuyPrice"] = "突破买点"
_core.COLUMN_NAMES["StopLoss"] = "止损位"

_core.COLUMN_WIDTHS["EntrySignal"] = 112
_core.COLUMN_WIDTHS["EntryZone"] = 112
_core.COLUMN_WIDTHS["BreakoutBuyPrice"] = 92
_core.COLUMN_WIDTHS["StopLoss"] = 78

# Re-export the patched values from this module as well, so tests and callers
# importing ``gui`` see exactly what the running ScannerGUI uses.
DISPLAY_COLUMNS = _core.DISPLAY_COLUMNS
COLUMN_NAMES = _core.COLUMN_NAMES
COLUMN_WIDTHS = _core.COLUMN_WIDTHS
ScannerGUI = _core.ScannerGUI
main = _core.main


if __name__ == "__main__":
    main()
