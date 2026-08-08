from __future__ import annotations

import unittest

from classification import etf_theme_key, etf_tracking_key, model_classification, theme_cluster


class ETFClassificationIntegrityTests(unittest.TestCase):
    def test_tracking_key_is_idempotent_when_sector_repeats_name_theme(self):
        key = etf_tracking_key(
            name="上海国企ETF汇添富",
            sector="上海国企",
            ticker="510810.SH",
        )
        self.assertEqual(key, "上海国企")

    def test_manager_after_etf_marker_is_not_part_of_tracking_key(self):
        self.assertEqual(
            etf_tracking_key(name="港股通科技ETF万家", ticker="159251.SZ"),
            "港股通科技",
        )
        self.assertEqual(
            etf_theme_key(name="港股通科技ETF万家", ticker="159251.SZ"),
            "港股科技",
        )

    def test_cashflow_factor_etfs_share_one_theme(self):
        self.assertEqual(
            etf_theme_key(name="自由现金流ETF平安", ticker="159233.SZ"),
            "现金流因子",
        )
        self.assertEqual(
            etf_theme_key(name="中证现金流ETF大成", ticker="159235.SZ"),
            "现金流因子",
        )

    def test_model_classification_does_not_duplicate_fallback(self):
        classification = model_classification(
            is_etf=True,
            name="深证主板50ETF南方",
            sector="深证主板50",
            ticker="159578.SZ",
        )
        self.assertEqual(classification, "深证主板50")
        cluster = theme_cluster(
            is_etf=True,
            name="深证主板50ETF南方",
            sector="深证主板50",
            classification=classification,
            ticker="159578.SZ",
        )
        self.assertNotEqual(cluster, "深证主板50深证主板50")


if __name__ == "__main__":
    unittest.main()
