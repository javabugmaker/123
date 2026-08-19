from __future__ import annotations

import logging
import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

import config
import downloader
import signal_lifecycle


def _frame(day: str) -> pd.DataFrame:
    return pd.DataFrame(
        {"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1.0]},
        index=pd.to_datetime([day]),
    )


class FreeEODLagBoundTests(unittest.TestCase):
    def _frames(self, day: str, count: int) -> dict[str, pd.DataFrame]:
        return {f"{index:06d}.SZ": _frame(day) for index in range(count)}

    def test_one_trading_day_provider_lag_remains_allowed(self) -> None:
        frames = self._frames("2026-08-18", 20)
        with (
            patch.object(downloader, "_is_a_share_market_closed", return_value=True),
            patch.object(
                downloader,
                "_latest_completed_trading_day",
                return_value=date(2026, 8, 19),
            ),
        ):
            downloader._assert_free_eod_coherence(frames)

    def test_coherent_multi_day_stale_universe_fails_before_analysis(self) -> None:
        frames = self._frames("2026-08-14", 20)
        with (
            patch.object(downloader, "_is_a_share_market_closed", return_value=True),
            patch.object(
                downloader,
                "_latest_completed_trading_day",
                return_value=date(2026, 8, 19),
            ),
        ):
            with self.assertRaises(downloader.DownloadError) as caught:
                downloader._assert_free_eod_coherence(frames)
        self.assertIn("过度陈旧", str(caught.exception))
        self.assertIn("超过允许", str(caught.exception))

    def test_mixed_settlement_still_fails(self) -> None:
        frames = self._frames("2026-08-19", 73)
        frames.update(
            {
                f"9{index:05d}.SZ": _frame("2026-08-18")
                for index in range(27)
            }
        )
        with (
            patch.object(downloader, "_is_a_share_market_closed", return_value=True),
            patch.object(
                downloader,
                "_latest_completed_trading_day",
                return_value=date(2026, 8, 19),
            ),
        ):
            with self.assertRaises(downloader.DownloadError) as caught:
                downloader._assert_free_eod_coherence(frames)
        self.assertIn("混合结算", str(caught.exception))

    def test_future_dominant_date_fails_closed(self) -> None:
        frames = self._frames("2026-08-20", 20)
        with (
            patch.object(downloader, "_is_a_share_market_closed", return_value=True),
            patch.object(
                downloader,
                "_latest_completed_trading_day",
                return_value=date(2026, 8, 19),
            ),
        ):
            with self.assertRaises(downloader.DownloadError) as caught:
                downloader._assert_free_eod_coherence(frames)
        self.assertIn("主日期晚于目标交易日", str(caught.exception))


class TradeFreshnessGateTests(unittest.TestCase):
    @staticmethod
    def _row(state: str, age: int | None) -> pd.DataFrame:
        payload: dict[str, list[object]] = {
            "DecisionState": [state],
            "RankingEligibility": [
                "推荐" if state == "READY" else "谨慎候选" if state == "CAUTIOUS" else "观察"
            ],
            "TradeReadiness": [
                "推荐" if state == "READY" else "谨慎候选" if state == "CAUTIOUS" else "观察"
            ],
            "RankingScore": [42.1234],
            "TradeReadinessReason": ["原执行条件"],
            "DecisionReason": ["原执行条件"],
            "RankingReason": ["原研究排序"],
            "ActionSuggestion": ["按原条件执行"],
            "RiskNote": ["原风险说明"],
            "OperationAdvice": ["原操作建议"],
        }
        if age is not None:
            payload["DataTradingAgeDays"] = [age]
        return pd.DataFrame(payload)

    def test_current_session_ready_stays_ready(self) -> None:
        frame = self._row("READY", 0)
        original_score = float(frame.loc[0, "RankingScore"])
        signal_lifecycle._apply_trade_freshness_gate(frame)
        self.assertEqual(frame.loc[0, "DecisionState"], "READY")
        self.assertTrue(bool(frame.loc[0, "TradeFreshnessPassed"]))
        self.assertFalse(bool(frame.loc[0, "TradeFreshnessGateApplied"]))
        self.assertEqual(float(frame.loc[0, "RankingScore"]), original_score)

    def test_one_day_old_ready_is_research_only(self) -> None:
        frame = self._row("READY", 1)
        original_score = float(frame.loc[0, "RankingScore"])
        signal_lifecycle._apply_trade_freshness_gate(frame)
        self.assertEqual(frame.loc[0, "DecisionState"], "OBSERVE")
        self.assertEqual(frame.loc[0, "RankingEligibility"], "观察")
        self.assertTrue(bool(frame.loc[0, "TradeFreshnessGateApplied"]))
        self.assertIn("行情落后 1 个交易日", str(frame.loc[0, "TradeReadinessReason"]))
        self.assertEqual(float(frame.loc[0, "RankingScore"]), original_score)

    def test_old_cautious_signal_is_demoted(self) -> None:
        frame = self._row("CAUTIOUS", 5)
        signal_lifecycle._apply_trade_freshness_gate(frame)
        self.assertEqual(frame.loc[0, "DecisionState"], "OBSERVE")
        self.assertTrue(bool(frame.loc[0, "TradeFreshnessGateApplied"]))

    def test_observe_row_is_not_reclassified(self) -> None:
        frame = self._row("OBSERVE", 3)
        signal_lifecycle._apply_trade_freshness_gate(frame)
        self.assertEqual(frame.loc[0, "DecisionState"], "OBSERVE")
        self.assertFalse(bool(frame.loc[0, "TradeFreshnessGateApplied"]))
        self.assertFalse(bool(frame.loc[0, "TradeFreshnessPassed"]))

    def test_legacy_row_without_age_keeps_old_execution_semantics(self) -> None:
        frame = self._row("READY", None)
        signal_lifecycle._apply_trade_freshness_gate(frame)
        self.assertEqual(frame.loc[0, "DecisionState"], "READY")
        self.assertEqual(frame.loc[0, "TradeFreshnessStatus"], "LEGACY_UNKNOWN")
        self.assertFalse(bool(frame.loc[0, "TradeFreshnessApplicable"]))


class LoggingHardeningTests(unittest.TestCase):
    def test_module_logger_does_not_propagate_to_parent_handler(self) -> None:
        name = "institution_scanner.test_v58_no_duplicate"
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        try:
            configured = config.setup_logging(
                name,
                log_to_file=False,
                level=logging.DEBUG,
            )
            self.assertFalse(configured.propagate)
            self.assertEqual(len(configured.handlers), 1)
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()


if __name__ == "__main__":
    unittest.main()
