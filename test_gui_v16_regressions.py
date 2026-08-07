import unittest
from unittest.mock import Mock

import gui


class GUIV16FilterRegressionTests(unittest.TestCase):
    def _scanner(self, headers):
        scanner = object.__new__(gui.ScannerGUI)
        scanner._csv_headers = list(headers)
        return scanner

    def test_fundamental_filter_uses_actual_quality_fields(self):
        headers = [
            "Ticker",
            "AssetType",
            "QualityDataAvailable",
            "QualityGate",
            "QualityDataCompleteness",
            "RankingScore",
        ]
        scanner = self._scanner(headers)
        indexes = {name: index for index, name in enumerate(headers)}

        passed = ["000001.SZ", "stock", "True", "True", "0.8", "42"]
        failed = ["000002.SZ", "stock", "True", "False", "0.7", "39"]
        missing = ["000003.SZ", "stock", "False", "True", "0.0", "38"]
        etf = ["510300.SH", "etf", "False", "True", "0.0", "41"]

        base = ("全部板块", "全部行业", "全部质量", "全部阶段", "全部买点", "全部资格", "全部类型")
        self.assertTrue(scanner._row_matches_filters(indexes, passed, "", filter_values=base + ("通过", "全部等级", "全部回测", "全部分数")))
        self.assertTrue(scanner._row_matches_filters(indexes, failed, "", filter_values=base + ("未通过", "全部等级", "全部回测", "全部分数")))
        self.assertTrue(scanner._row_matches_filters(indexes, missing, "", filter_values=base + ("数据缺失", "全部等级", "全部回测", "全部分数")))
        self.assertTrue(scanner._row_matches_filters(indexes, etf, "", filter_values=base + ("ETF不适用", "全部等级", "全部回测", "全部分数")))

    def test_asset_tier_backtest_and_score_filters_compose(self):
        headers = [
            "Ticker",
            "AssetType",
            "InstitutionalTier",
            "BacktestConfidenceTier",
            "RankingScore",
        ]
        scanner = self._scanner(headers)
        indexes = {name: index for index, name in enumerate(headers)}
        row = ["000001.SZ", "stock", "B级观察", "高可信度", "36.5"]
        filters = (
            "全部板块",
            "全部行业",
            "全部质量",
            "全部阶段",
            "全部买点",
            "全部资格",
            "股票",
            "全部基本面",
            "B级观察",
            "高可信度",
            "≥35",
        )
        self.assertTrue(scanner._row_matches_filters(indexes, row, "", filter_values=filters))
        self.assertFalse(scanner._row_matches_filters(indexes, row, "", filter_values=filters[:-1] + ("≥40",)))
        self.assertFalse(scanner._row_matches_filters(indexes, row, "", filter_values=filters[:6] + ("ETF",) + filters[7:]))

    def test_legacy_quality_filter_tuple_remains_compatible(self):
        headers = ["Ticker", "Quality"]
        scanner = self._scanner(headers)
        indexes = {name: index for index, name in enumerate(headers)}
        row = ["000001.SZ", "强候选"]
        legacy_filters = (
            "全部板块",
            "全部行业",
            "强候选",
            "全部阶段",
            "全部买点",
            "全部资格",
        )
        self.assertTrue(scanner._row_matches_filters(indexes, row, "", filter_values=legacy_filters))

    def test_clear_filters_resets_new_filter_state(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner.search = Mock()
        scanner.sector_filter = Mock()
        scanner.industry_filter = Mock()
        scanner.quality_filter = Mock()
        scanner.asset_filter = Mock()
        scanner.fundamental_filter = Mock()
        scanner.tier_filter = Mock()
        scanner.backtest_filter = Mock()
        scanner.score_filter = Mock()
        scanner.stage_filter = Mock()
        scanner.entry_filter = Mock()
        scanner.eligibility_filter = Mock()
        scanner._filter_job = None
        scanner.root = Mock()
        scanner._render_cached_rows = Mock()

        scanner.clear_filters()

        scanner.asset_filter.set.assert_called_once_with("全部类型")
        scanner.fundamental_filter.set.assert_called_once_with("全部基本面")
        scanner.tier_filter.set.assert_called_once_with("全部等级")
        scanner.backtest_filter.set.assert_called_once_with("全部回测")
        scanner.score_filter.set.assert_called_once_with("全部分数")
        scanner._render_cached_rows.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
