"""Additive quality provenance for publication DataFrames.

This enrichment is diagnostic/output-only. It never rewrites production score,
rank, eligibility or the existing QualityGate column. The goal is to preserve
tri-state and annual/interim ROE semantics at the CSV/Parquet boundary without
expanding the legacy ScanResult god object.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fundamental_data import fundamental_data_path
from fundamental_quality import calculate_quality, load_fundamental_data

from institution_scanner.quality_policy import EvidenceStatus


QUALITY_PROVENANCE_COLUMNS = (
    "InterimROE",
    "LatestAnnualROE",
    "LatestAnnualROEPeriod",
    "ROEHardGateValue",
    "ROEHardGateSource",
    "QualityROEStatus",
    "QualityGrossMarginStatus",
    "QualityNetProfitStatus",
    "QualityGateStatus",
    "QualityGateEvidenceCompleteness",
    "FinancialFieldCoverage",
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        return ""
    return str(value).strip()


def stamp_quality_provenance(frame: pd.DataFrame) -> pd.DataFrame:
    """Stamp additive quality semantics while preserving all production columns."""
    if frame is None or frame.empty or "Ticker" not in frame.columns:
        return frame

    path_value = fundamental_data_path()
    rows: dict[str, dict[str, Any]] = {}
    if path_value:
        path = Path(path_value)
        if path.exists():
            rows = load_fundamental_data(str(path.resolve()))

    is_etf = (
        frame.get("IsETF", pd.Series(False, index=frame.index))
        .fillna(False)
        .astype(bool)
    )
    if "AssetType" in frame.columns:
        is_etf |= frame["AssetType"].fillna("").astype(str).str.lower().eq("etf")

    values: dict[str, list[Any]] = {column: [] for column in QUALITY_PROVENANCE_COLUMNS}
    for index, ticker_value in frame["Ticker"].items():
        ticker = _text(ticker_value).upper()
        if bool(is_etf.loc[index]):
            payload = {
                "InterimROE": np.nan,
                "LatestAnnualROE": np.nan,
                "LatestAnnualROEPeriod": "",
                "ROEHardGateValue": np.nan,
                "ROEHardGateSource": "NOT_APPLICABLE",
                "QualityROEStatus": str(EvidenceStatus.NOT_APPLICABLE),
                "QualityGrossMarginStatus": str(EvidenceStatus.NOT_APPLICABLE),
                "QualityNetProfitStatus": str(EvidenceStatus.NOT_APPLICABLE),
                "QualityGateStatus": str(EvidenceStatus.NOT_APPLICABLE),
                "QualityGateEvidenceCompleteness": 0.0,
                "FinancialFieldCoverage": np.nan,
            }
        else:
            raw = rows.get(ticker, {})
            quality = calculate_quality(raw, ticker) if raw else None
            payload = {
                "InterimROE": quality.interim_roe if quality else np.nan,
                "LatestAnnualROE": quality.latest_annual_roe if quality else np.nan,
                "LatestAnnualROEPeriod": (
                    quality.latest_annual_roe_period if quality else ""
                ),
                "ROEHardGateValue": quality.roe_hard_gate_value if quality else np.nan,
                "ROEHardGateSource": (
                    quality.roe_hard_gate_source if quality else "UNKNOWN"
                ),
                "QualityROEStatus": quality.roe_status if quality else "UNKNOWN",
                "QualityGrossMarginStatus": (
                    quality.gross_margin_status if quality else "UNKNOWN"
                ),
                "QualityNetProfitStatus": (
                    quality.net_profit_status if quality else "UNKNOWN"
                ),
                "QualityGateStatus": (
                    quality.quality_gate_status if quality else "UNKNOWN"
                ),
                "QualityGateEvidenceCompleteness": (
                    quality.quality_gate_evidence_completeness if quality else 0.0
                ),
                "FinancialFieldCoverage": (
                    quality.financial_field_coverage if quality else np.nan
                ),
            }
        for column in QUALITY_PROVENANCE_COLUMNS:
            values[column].append(payload[column])

    for column, column_values in values.items():
        frame[column] = column_values
    return frame
