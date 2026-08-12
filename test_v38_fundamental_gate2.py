from __future__ import annotations

import unittest

import numpy as np

import config
from fundamental_quality import calculate_quality


def row(
    *,
    industry: str,
    roe: float,
    margin_pct: float = np.nan,
    y1: float,
    y2: float,
    y3: float,
    holding: str | None = None,
    holding_periods: int = 0,
):
    return {
        "Ticker": "000001.SZ",
        "Industry": industry,
        "ROE": roe,
        "GrossMargin": 30.0,
        "IndustryGrossMarginPercentile": margin_pct,
        "InstitutionHoldingTrend": holding,
        "InstitutionHoldingPeriods": holding_periods,
        "NetProfitY1": y1,
        "NetProfitY2": y2,
        "NetProfitY3": y3,
    }


class FundamentalGateV38Tests(unittest.TestCase):
    def test_cyclical_trough_recovery_can_pass_without_lowering_general_gate(self):
        quality = calculate_quality(
            row(industry="水泥", roe=7.0, margin_pct=0.45, y1=300.0, y2=100.0, y3=150.0)
        )
        self.assertEqual(quality.quality_profile, "CYCLICAL")
        self.assertEqual(quality.profit_trend_status, "RECOVERY")
        self.assertTrue(quality.cyclical_quality_override)
        self.assertTrue(quality.quality_gate)

        general = calculate_quality(
            row(industry="通用设备", roe=7.0, margin_pct=0.20, y1=300.0, y2=200.0, y3=100.0)
        )
        self.assertEqual(general.quality_profile, "GENERAL")
        self.assertFalse(general.quality_gate)

    def test_cyclical_loss_or_bad_margin_still_fails(self):
        loss = calculate_quality(
            row(industry="造纸", roe=-10.0, margin_pct=0.40, y1=-20.0, y2=-30.0, y3=-40.0)
        )
        self.assertFalse(loss.quality_gate)
        bad_margin = calculate_quality(
            row(industry="饲料", roe=8.0, margin_pct=0.70, y1=120.0, y2=60.0, y3=90.0)
        )
        self.assertFalse(bad_margin.quality_gate)

    def test_financial_profile_does_not_require_gross_margin(self):
        bank = calculate_quality(
            row(industry="银行Ⅱ", roe=12.0, y1=130.0, y2=120.0, y3=110.0)
        )
        self.assertEqual(bank.quality_profile, "FINANCIAL")
        self.assertTrue(bank.quality_gate)
        self.assertTrue(bank.gross_margin_factor)
        self.assertAlmostEqual(bank.quality_data_completeness, 2 / 3, places=4)

        weak_broker = calculate_quality(
            row(industry="证券Ⅱ", roe=2.0, y1=130.0, y2=100.0, y3=-20.0)
        )
        self.assertFalse(weak_broker.quality_gate)

    def test_defensive_profile_allows_small_profit_dip_not_structural_decline(self):
        resilient = calculate_quality(
            row(industry="环境治理", roe=8.0, margin_pct=0.90, y1=95.0, y2=100.0, y3=80.0)
        )
        self.assertEqual(resilient.quality_profile, "DEFENSIVE")
        self.assertEqual(resilient.profit_trend_status, "RESILIENT")
        self.assertTrue(resilient.quality_gate)

        deteriorating = calculate_quality(
            row(industry="燃气Ⅱ", roe=8.0, y1=50.0, y2=100.0, y3=120.0)
        )
        self.assertEqual(deteriorating.profit_trend_status, "DETERIORATING")
        self.assertFalse(deteriorating.quality_gate)

    def test_institution_coverage_is_supporting_not_a_standalone_fundamental_veto(self):
        quality = calculate_quality(
            row(
                industry="铁路公路",
                roe=11.0,
                y1=95.0,
                y2=100.0,
                y3=80.0,
                holding="decreasing",
                holding_periods=3,
            )
        )
        self.assertTrue(quality.quality_gate)
        self.assertEqual(quality.institution_holding_status, "FAIL")
        self.assertLess(quality.quality_multiplier, 1.0)
        self.assertIn("不单独否决", quality.quality_reason)

    def test_v38_policy_survives_later_model_and_pipeline_versions(self):
        self.assertTrue(
            any(f"v{version}" in config.SCORING_VERSION for version in range(38, 100))
        )
        self.assertTrue(
            any(f"v{version}" in config.PIPELINE_VERSION for version in range(38, 100))
        )
        self.assertIn("v38", config.FUNDAMENTAL_GATE_VERSION)
        self.assertIn("v37", config.GUI_VERSION)
        self.assertIn("v36", config.MARKET_DATA_VERSION)


if __name__ == "__main__":
    unittest.main()
