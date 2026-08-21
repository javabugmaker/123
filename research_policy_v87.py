"""Vectorized directional-research product policy.

The canonical name/theme exclusions remain auditable, while a conservative
two-factor behaviour check catches unlabelled cash-equivalent ETFs.  Ordinary
equity-factor ETFs are not excluded merely because their names contain the word
``现金``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from classification import (
    ETF_RESEARCH_EXCLUDED_KEYWORDS,
    ETF_RESEARCH_EXCLUDED_LABELS,
    etf_theme_key,
)
from config import (
    ETF_CASH_EQUIVALENT_MAX_ABS_RETURN_20D_PCT,
    ETF_CASH_EQUIVALENT_MAX_ATR_PCT,
)

_NULLISH_TEXT = frozenset({"", "nan", "none", "nat", "<na>"})
_TRUTHY_TEXT = frozenset({"true", "1", "yes", "y", "是"})


def _column(frame: pd.DataFrame, column: str, default: object) -> pd.Series:
    """Return a position-indexed series so duplicate source indexes are safe."""
    if column in frame.columns:
        return pd.Series(frame[column].to_numpy(copy=False), copy=False)
    return pd.Series(np.full(len(frame), default, dtype=object), copy=False)


def _text(values: pd.Series) -> pd.Series:
    text = values.astype("string").fillna("").str.strip()
    return text.mask(text.str.lower().isin(_NULLISH_TEXT), "")


def _truthy(values: pd.Series) -> np.ndarray:
    return (
        values.astype("string")
        .fillna("")
        .str.strip()
        .str.lower()
        .isin(_TRUTHY_TEXT)
        .to_numpy(dtype=bool)
    )


def asset_is_etf(frame: pd.DataFrame) -> np.ndarray:
    asset_type = _text(_column(frame, "AssetType", ""))
    return _truthy(_column(frame, "IsETF", False)) | asset_type.str.lower().eq(
        "etf"
    ).to_numpy(dtype=bool)


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(_column(frame, column, np.nan), errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )


def vectorized_etf_research_policy(
    frame: pd.DataFrame,
    is_etf: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Return position-aligned eligibility and exclusion reasons.

    Behavioural exclusion requires *both* exceptionally low ATR and an almost
    flat 20-day return.  Missing diagnostics never manufacture an exclusion;
    the explicit name/theme policy still applies independently.
    """
    row_count = len(frame)
    eligible = np.ones(row_count, dtype=bool)
    reasons = np.full(row_count, "", dtype=object)
    etf_mask = asset_is_etf(frame) if is_etf is None else np.asarray(is_etf, dtype=bool)
    if etf_mask.shape != (row_count,):
        raise ValueError("is_etf must be position-aligned with frame")
    etf_positions = np.flatnonzero(etf_mask)
    if not etf_positions.size:
        return eligible, reasons

    etf_frame = frame.iloc[etf_positions]
    name = _text(_column(etf_frame, "Name", ""))
    industry = _text(_column(etf_frame, "Industry", ""))
    sector = _text(_column(etf_frame, "Sector", ""))
    classification_column = (
        "ModelClassification"
        if "ModelClassification" in etf_frame.columns
        else "ETFTheme"
    )
    resolved = _text(_column(etf_frame, classification_column, ""))

    unresolved = np.flatnonzero(resolved.eq("").to_numpy(dtype=bool))
    if unresolved.size:
        ticker = _text(_column(etf_frame, "Ticker", ""))
        resolved_values = resolved.to_numpy(dtype=object, copy=True)
        name_values = name.to_numpy(dtype=object, copy=False)
        industry_values = industry.to_numpy(dtype=object, copy=False)
        sector_values = sector.to_numpy(dtype=object, copy=False)
        ticker_values = ticker.to_numpy(dtype=object, copy=False)
        for position in unresolved:
            index = int(position)
            resolved_values[index] = etf_theme_key(
                name=name_values[index],
                industry=industry_values[index],
                sector=sector_values[index],
                ticker=ticker_values[index],
            )
        resolved = pd.Series(resolved_values, copy=False, dtype="string")

    combined = (
        name.str.upper()
        + " "
        + industry.str.upper()
        + " "
        + sector.str.upper()
        + " "
        + resolved.str.upper()
    )
    exact_exclusion = resolved.isin(ETF_RESEARCH_EXCLUDED_LABELS).to_numpy(dtype=bool)
    if np.any(exact_exclusion):
        excluded = etf_positions[exact_exclusion]
        eligible[excluded] = False
        reasons[excluded] = np.char.add(
            "ETF分类排除：",
            resolved.to_numpy(dtype=object, copy=False)[exact_exclusion].astype(str),
        )

    unmatched = ~exact_exclusion
    for keyword in ETF_RESEARCH_EXCLUDED_KEYWORDS:
        keyword_match = unmatched & combined.str.contains(
            str(keyword).upper(), regex=False, na=False
        ).to_numpy(dtype=bool)
        if np.any(keyword_match):
            excluded = etf_positions[keyword_match]
            eligible[excluded] = False
            reasons[excluded] = f"ETF现金管理产品排除：{keyword}"
            unmatched &= ~keyword_match

    # Use whichever current-schema return field is available.  Report rows use
    # RecentReturn20D; lifecycle-enriched rows also expose Return20D.
    close = _numeric(etf_frame, "Close")
    atr14 = _numeric(etf_frame, "ATR14")
    recent20 = _numeric(etf_frame, "RecentReturn20D")
    if "Return20D" in etf_frame.columns:
        recent20 = recent20.where(recent20.notna(), _numeric(etf_frame, "Return20D"))
    atr_pct = atr14.div(close).mul(100.0)
    behavioural = (
        unmatched
        & close.gt(0.0).to_numpy(dtype=bool)
        & atr14.gt(0.0).to_numpy(dtype=bool)
        & atr_pct.le(float(ETF_CASH_EQUIVALENT_MAX_ATR_PCT)).to_numpy(dtype=bool)
        & recent20.abs()
        .le(float(ETF_CASH_EQUIVALENT_MAX_ABS_RETURN_20D_PCT))
        .to_numpy(dtype=bool)
    )
    if np.any(behavioural):
        excluded = etf_positions[behavioural]
        eligible[excluded] = False
        detail = (
            "ETF现金等价特征排除：ATR/收盘="
            + atr_pct.round(4).astype("string")
            + "%，20日收益="
            + recent20.round(4).astype("string")
            + "%"
        )
        reasons[excluded] = detail.to_numpy(dtype=object, copy=False)[behavioural]

    return eligible, reasons
