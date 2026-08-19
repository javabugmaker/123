from __future__ import annotations

import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

import signal_lifecycle
from trading_calendar import latest_completed_trading_day, trading_age_days


class TradingCalendarIntegrityTests(unittest.TestCase):
    def test_preclose_current_calendar_date_is_future_to_completed_session(self) -> None:
        now = datetime(2026, 8, 19, 14, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(latest_completed_trading_day(now), date(2026, 8, 18))
        self.assertEqual(trading_age_days(date(2026, 8, 19), now), -1)
        self.assertEqual(trading_age_days(date(2026, 8, 18), now), 0)

    def test_postclose_current_date_is_age_zero(self) -> None:
        now = datetime(2026, 8, 19, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(latest_completed_trading_day(now), date(2026, 8, 19))
        self.assertEqual(trading_age_days(date(2026, 8, 19), now), 0)

    def test_future_dated_execution_age_fails_trade_freshness_gate(self) -> None:
        frame = pd.DataFrame(
            {
                "Ticker": ["000001.SZ"],
                "DecisionState": ["READY"],
                "RankingEligibility": ["推荐"],
                "TradeReadiness": ["推荐"],
                "TradeReadinessReason": ["原始推荐"],
                "RankingReason": ["研究排序"],
                "ActionSuggestion": ["可执行"],
                "RiskNote": [""],
                "OperationAdvice": ["执行"],
                "RankingScore": [80.0],
                "DataTradingAgeDays": [-1],
            }
        )

        result = signal_lifecycle._apply_trade_freshness_gate(frame.copy())

        self.assertTrue(bool(result.iloc[0]))
        # Gate mutates the supplied frame; verify full lifecycle behavior through
        # finalize_signal_ranking-compatible helper input instead.
        checked = frame.copy()
        signal_lifecycle._apply_trade_freshness_gate(checked)
        self.assertEqual(checked.loc[0, "DecisionState"], "OBSERVE")
        self.assertEqual(checked.loc[0, "TradeFreshnessStatus"], "UNKNOWN")
        self.assertFalse(bool(checked.loc[0, "TradeFreshnessPassed"]))
        self.assertEqual(float(checked.loc[0, "RankingScore"]), 80.0)


if __name__ == "__main__":
    unittest.main()
