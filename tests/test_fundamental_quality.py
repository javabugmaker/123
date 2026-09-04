from __future__ import annotations

import numpy as np
import pandas as pd

from analytics_core import _decision_quality_multiplier
from config import QUALITY_MULTIPLIER_PASS, QUALITY_MULTIPLIER_UNKNOWN
from fundamental_quality import calculate_quality
from signal_lifecycle_core import finalize_signal_ranking


def _complete_row() -> dict[str, object]:
    return {
        "Ticker": "000001.SZ",
        "Industry": "软件服务",
        "LatestReportPeriod": "2026-09-30",
        "LatestAnnouncementDate": "2026-10-30",
        "LatestReportType": "三季报",
        "FundamentalProvider": "akshare",
        "FundamentalDataStatus": "CURRENT",
        "ROE": 7.0,
        "InterimROE": 7.0,
        "LatestAnnualROE": 15.0,
        "LatestAnnualROEPeriod": "2025-12-31",
        "ROEHardGateValue": 15.0,
        "ROEHardGateSource": "LATEST_ANNUAL_ROE",
        "GrossMargin": 45.0,
        "NetProfitLatest": 390.0,
        "RevenueLatest": 1200.0,
        "NetProfitYoY": 18.0,
        "DebtToAssets": np.nan,
        "OperatingCashFlowToNetProfit": 1.2,
        "FinancialFieldCoverage": 0.8571,
        "NetProfitY1": 300.0,
        "NetProfitY2": 240.0,
        "NetProfitY3": 180.0,
        "IndustryGrossMarginPercentile": 0.10,
        "InstitutionHoldingTrend": "",
        "InstitutionHoldingPeriods": np.nan,
    }


def test_akshare_quality_no_longer_depends_on_institution_coverage() -> None:
    quality = calculate_quality(_complete_row())

    assert quality.institution_holding_status == "UNKNOWN"
    assert quality.quality_gate is True
    assert quality.quality_gate_status == "PASS"
    assert quality.roe_status == "PASS"
    assert quality.quality_hard_data_complete is True
    assert quality.quality_data_completeness == 1.0
    assert quality.quality_gate_evidence_completeness == 1.0
    assert quality.quality_multiplier == QUALITY_MULTIPLIER_PASS
    assert quality.roe_hard_gate_value == 15.0
    assert quality.interim_roe == 7.0
    assert "机构覆盖" not in quality.quality_reason


def test_interim_roe_does_not_fail_a_full_year_quality_threshold() -> None:
    row = _complete_row()
    row["LatestReportPeriod"] = "2026-06-30"
    row["LatestReportType"] = "半年报"
    row["ROE"] = 2.88
    row["InterimROE"] = 2.88
    row["LatestAnnualROE"] = 12.5
    row["ROEHardGateValue"] = 12.5

    quality = calculate_quality(row)

    assert quality.roe == 2.88
    assert quality.interim_roe == 2.88
    assert quality.roe_hard_gate_value == 12.5
    assert quality.roe_status == "PASS"
    assert quality.quality_gate is True
    assert "中期ROE 2.88%（诊断）" in quality.quality_reason


def test_akshare_interim_report_without_annual_roe_is_unknown_not_fail() -> None:
    row = _complete_row()
    row["LatestAnnualROE"] = np.nan
    row["LatestAnnualROEPeriod"] = ""
    row["ROEHardGateValue"] = np.nan
    row["ROEHardGateSource"] = "UNKNOWN"

    quality = calculate_quality(row)

    assert quality.roe_status == "UNKNOWN"
    assert quality.quality_gate_status == "UNKNOWN"
    assert quality.quality_gate is True
    assert quality.quality_hard_data_complete is False
    assert quality.quality_multiplier == QUALITY_MULTIPLIER_UNKNOWN
    assert "中期ROE仅作诊断" in quality.quality_reason


def test_missing_announcement_date_is_neutral_unknown_not_a_false_pass() -> None:
    row = _complete_row()
    row["LatestAnnouncementDate"] = ""

    quality = calculate_quality(row)

    assert quality.quality_gate is True
    assert quality.quality_gate_status == "UNKNOWN"
    assert quality.quality_hard_data_complete is False
    assert quality.quality_multiplier == QUALITY_MULTIPLIER_UNKNOWN
    assert "报告期与公告日可追溯" in quality.quality_reason


def test_report_past_its_filing_deadline_cannot_receive_full_multiplier() -> None:
    row = _complete_row()
    row["FundamentalDataStatus"] = "STALE"

    quality = calculate_quality(row)

    assert quality.quality_gate is True
    assert quality.quality_gate_status == "UNKNOWN"
    assert quality.quality_hard_data_complete is False
    assert quality.quality_multiplier == QUALITY_MULTIPLIER_UNKNOWN
    assert "财报披露时效" in quality.quality_reason


def test_ranking_and_backtest_do_not_reintroduce_institution_holding_dependency() -> None:
    row = _complete_row()
    row.update(
        {
            "Score": 80.0,
            "FinalScore": 80.0,
            "InstitutionalScore": 80.0,
            "IsETF": False,
            "QualityApplicable": True,
            "QualityProfile": "GENERAL",
            "QualityGate": True,
            "QualityDataAvailable": True,
            "QualityHardDataComplete": True,
            "QualityMultiplier": QUALITY_MULTIPLIER_PASS,
            "QualityROE": True,
            "QualityGrossMargin": True,
            "QualityNetProfit": True,
            "InstitutionHoldingStatus": "UNKNOWN",
            "PassedFilters": True,
            "UniverseEligible": True,
        }
    )
    frame = pd.DataFrame([row])

    ranked = finalize_signal_ranking(frame)
    replay_multiplier = _decision_quality_multiplier(
        frame.drop(columns="QualityHardDataComplete"),
        is_etf=pd.Series(False, index=frame.index),
        quality_available=pd.Series(True, index=frame.index),
    )

    assert ranked.loc[0, "InstitutionHoldingStatus"] == "UNKNOWN"
    assert ranked.loc[0, "QualityMultiplier"] == QUALITY_MULTIPLIER_PASS
    assert replay_multiplier.iloc[0] == QUALITY_MULTIPLIER_PASS
