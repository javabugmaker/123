"""Canonical fundamental-quality evidence semantics.

The public AKShare/Eastmoney ROE field is a report-period return on equity. Interim
reports therefore must not be compared directly with full-year ROE thresholds.
This module centralises that distinction so the scanner has one auditable rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import numpy as np
import pandas as pd


class EvidenceStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class ROEEvidence:
    reported_roe: float = np.nan
    interim_roe: float = np.nan
    latest_annual_roe: float = np.nan
    latest_annual_period: str = ""
    hard_gate_roe: float = np.nan
    hard_gate_source: str = "UNKNOWN"
    latest_report_is_interim: bool = False


def _number(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return np.nan
    return parsed if np.isfinite(parsed) else np.nan


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        return ""
    parsed = str(value).strip()
    return "" if parsed.lower() in {"nan", "none", "null", "<na>"} else parsed


def _is_annual_report(report_type: str, report_period: str) -> bool:
    normalized_type = _text(report_type)
    if normalized_type in {"年报", "ANNUAL", "FY", "Q4"}:
        return True
    parsed = pd.to_datetime(report_period, errors="coerce")
    return bool(
        pd.notna(parsed)
        and pd.Timestamp(parsed).month == 12
        and pd.Timestamp(parsed).day == 31
    )


def resolve_roe_evidence(values: dict[str, Any]) -> ROEEvidence:
    """Resolve the ROE value that may be used by a full-year quality hard gate.

    For current AKShare rows, interim ROE is diagnostic only. The hard gate uses
    the most recent already-announced annual ROE. If an old/legacy cache lacks
    the new provenance columns, its historical reported ROE remains a compatible
    fallback so old result files stay readable.
    """

    reported = _number(values.get("ROE"))
    annual = _number(values.get("LatestAnnualROE"))
    annual_period = _text(values.get("LatestAnnualROEPeriod"))
    explicit_gate = _number(values.get("ROEHardGateValue"))
    explicit_source = _text(values.get("ROEHardGateSource")).upper()
    report_period = _text(values.get("LatestReportPeriod"))
    report_type = _text(values.get("LatestReportType"))
    provider = _text(values.get("FundamentalProvider")).lower()
    annual_report = _is_annual_report(report_type, report_period)
    interim = reported if not annual_report else np.nan

    if np.isfinite(explicit_gate):
        hard_gate = explicit_gate
        source = explicit_source or "EXPLICIT"
    elif np.isfinite(annual):
        hard_gate = annual
        source = "LATEST_ANNUAL_ROE"
    elif annual_report and np.isfinite(reported):
        hard_gate = reported
        source = "REPORTED_ANNUAL_ROE"
    elif provider not in {"akshare"} and np.isfinite(reported):
        hard_gate = reported
        source = "LEGACY_REPORTED_ROE"
    else:
        hard_gate = np.nan
        source = "UNKNOWN"

    return ROEEvidence(
        reported_roe=reported,
        interim_roe=interim,
        latest_annual_roe=annual,
        latest_annual_period=annual_period,
        hard_gate_roe=hard_gate,
        hard_gate_source=source,
        latest_report_is_interim=not annual_report,
    )


def status_from_optional_bool(value: bool | None) -> EvidenceStatus:
    if value is True:
        return EvidenceStatus.PASS
    if value is False:
        return EvidenceStatus.FAIL
    return EvidenceStatus.UNKNOWN


def gate_status(*factors: bool | None) -> EvidenceStatus:
    if any(value is False for value in factors):
        return EvidenceStatus.FAIL
    if any(value is None for value in factors):
        return EvidenceStatus.UNKNOWN
    return EvidenceStatus.PASS
