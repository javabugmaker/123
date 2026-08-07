from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config import (
    INSTITUTION_HOLDING_MIN_PERIODS,
    QUALITY_MULTIPLIER_FAIL,
    QUALITY_MULTIPLIER_PASS,
    QUALITY_MULTIPLIER_UNKNOWN,
)
from fundamental_data import FUNDAMENTAL_COLUMNS, fundamental_data_path

FUNDAMENTAL_FACTOR_COLUMNS = tuple(
    column for column in FUNDAMENTAL_COLUMNS if column not in {"Ticker", "Industry"}
)


@dataclass(frozen=True)
class FundamentalQuality:
    ticker: str
    roe: float = np.nan
    gross_margin: float = np.nan
    institution_holding_trend: Any = None
    institution_holding_periods: float = np.nan
    net_profit_y1: float = np.nan
    net_profit_y2: float = np.nan
    net_profit_y3: float = np.nan
    industry_gross_margin_percentile: float = np.nan
    roe_factor: bool = False
    gross_margin_factor: bool = False
    institution_holding_factor: bool = False
    net_profit_factor: bool = False
    quality_score: float = np.nan
    quality_gate: bool = True
    quality_reason: str = "基本面数据缺失（中性）"
    data_available: bool = False
    institution_holding_status: str = "UNKNOWN"
    quality_data_completeness: float = 0.0
    quality_gate_reason: str = "基本面数据缺失（中性）"
    quality_multiplier: float = QUALITY_MULTIPLIER_UNKNOWN

    @property
    def valid_score(self) -> bool:
        return self.data_available and np.isfinite(self.quality_score)


def _ticker(value: Any) -> str:
    return str(value).strip().upper()


def _number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return np.nan
    return result if np.isfinite(result) else np.nan


def _trend_is_increasing(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {
            "increasing",
            "increase",
            "increased",
            "up",
            "上涨",
            "增加",
            "连续增加",
            "是",
            "true",
            "1",
        }
    number = _number(value)
    return bool(np.isfinite(number) and number > 0)


def _institution_holding_status(trend: Any, periods: float) -> str:
    """Classify institution-count evidence without treating missing history as failure."""
    if not np.isfinite(periods) or periods < INSTITUTION_HOLDING_MIN_PERIODS:
        return "UNKNOWN"
    if trend is None or (isinstance(trend, str) and not trend.strip()):
        return "UNKNOWN"
    if _trend_is_increasing(trend):
        return "PASS"
    if isinstance(trend, str):
        normalized = trend.strip().lower()
        if normalized in {
            "not_increasing",
            "decreasing",
            "decrease",
            "decreased",
            "down",
            "减少",
            "连续减少",
            "false",
            "0",
        }:
            return "FAIL"
        return "UNKNOWN"
    number = _number(trend)
    return "FAIL" if np.isfinite(number) and number <= 0 else "UNKNOWN"


def calculate_quality(row: pd.Series | dict[str, Any], ticker: str = "") -> FundamentalQuality:
    values = row.to_dict() if isinstance(row, pd.Series) else row
    normalized_ticker = _ticker(values.get("Ticker", ticker))
    numeric = {
        column: _number(values.get(column)) for column in FUNDAMENTAL_FACTOR_COLUMNS
    }
    trend = values.get("InstitutionHoldingTrend")
    holding_status = _institution_holding_status(
        trend, numeric["InstitutionHoldingPeriods"]
    )
    roe_available = np.isfinite(numeric["ROE"])
    gross_margin_available = np.isfinite(
        numeric["IndustryGrossMarginPercentile"]
    )
    profit_available = all(
        np.isfinite(numeric[column])
        for column in ("NetProfitY1", "NetProfitY2", "NetProfitY3")
    )
    holding_available = holding_status in {"PASS", "FAIL"}
    factors: dict[str, bool | None] = {
        "ROE>10%": numeric["ROE"] > 10.0 if roe_available else None,
        "毛利率行业前30%": (
            numeric["IndustryGrossMarginPercentile"] <= 0.30
            if gross_margin_available
            else None
        ),
        # AKShare exposes the number of institutions covering/holding the stock,
        # not the aggregate shares/value held. Keep the label semantically exact.
        "机构覆盖家数连续增加": (
            True if holding_status == "PASS" else False if holding_status == "FAIL" else None
        ),
        "近3年净利润非下降": (
            numeric["NetProfitY1"] >= numeric["NetProfitY2"] >= numeric["NetProfitY3"]
            if profit_available
            else None
        ),
    }
    passed = [name for name, value in factors.items() if value is True]
    failed = [name for name, value in factors.items() if value is False]
    unknown = [name for name, value in factors.items() if value is None]
    completeness = (
        float(roe_available)
        + float(gross_margin_available)
        + float(holding_available)
        + float(profit_available)
    ) / 4.0
    data_available = bool(roe_available or gross_margin_available or holding_available or profit_available)
    quality_gate = not failed
    if failed:
        quality_multiplier = QUALITY_MULTIPLIER_FAIL
    elif unknown:
        quality_multiplier = QUALITY_MULTIPLIER_UNKNOWN
    else:
        quality_multiplier = QUALITY_MULTIPLIER_PASS

    reason_parts: list[str] = []
    if failed:
        reason_parts.append("未通过：" + "、".join(failed))
    if holding_status == "UNKNOWN":
        reason_parts.append("机构覆盖家数历史不足（中性）")
    other_unknown = [name for name in unknown if name != "机构覆盖家数连续增加"]
    if other_unknown:
        reason_parts.append("数据不足：" + "、".join(other_unknown))
    if not reason_parts:
        reason_parts.append("全部通过")
    reason = "；".join(reason_parts)

    # Unknown factors must not disappear from the denominator and turn one
    # observed passing factor into a false 100/100 quality score. Shrink the
    # observed pass-rate toward neutral (50) according to data completeness.
    if passed or failed:
        observed_pass_rate = len(passed) / (len(passed) + len(failed)) * 100.0
        quality_score = round(
            50.0 + (observed_pass_rate - 50.0) * completeness,
            4,
        )
    else:
        quality_score = np.nan

    return FundamentalQuality(
        ticker=normalized_ticker,
        roe=numeric["ROE"],
        gross_margin=numeric["GrossMargin"],
        institution_holding_trend=trend,
        institution_holding_periods=numeric["InstitutionHoldingPeriods"],
        net_profit_y1=numeric["NetProfitY1"],
        net_profit_y2=numeric["NetProfitY2"],
        net_profit_y3=numeric["NetProfitY3"],
        industry_gross_margin_percentile=numeric["IndustryGrossMarginPercentile"],
        roe_factor=bool(factors["ROE>10%"]),
        gross_margin_factor=bool(factors["毛利率行业前30%"]),
        institution_holding_factor=bool(factors["机构覆盖家数连续增加"]),
        net_profit_factor=bool(factors["近3年净利润非下降"]),
        quality_score=quality_score,
        quality_gate=quality_gate,
        quality_reason=reason,
        data_available=data_available,
        institution_holding_status=holding_status,
        quality_data_completeness=round(completeness, 4),
        quality_gate_reason=reason,
        quality_multiplier=quality_multiplier,
    )


def _path_value() -> Path | None:
    return fundamental_data_path()


@lru_cache(maxsize=4)
def load_fundamental_data(path_value: str) -> dict[str, pd.Series]:
    path = Path(path_value)
    try:
        frame = pd.read_csv(path, dtype={"Ticker": str})
    except (OSError, UnicodeError, pd.errors.ParserError, ValueError):
        return {}
    required_columns = set(FUNDAMENTAL_COLUMNS) - {"Industry"}
    if not required_columns.issubset(frame.columns):
        return {}
    if "Industry" not in frame:
        frame["Industry"] = ""
    frame = frame.loc[:, FUNDAMENTAL_COLUMNS].copy()
    frame["Ticker"] = frame["Ticker"].map(_ticker)
    return {
        ticker: row
        for ticker, row in frame.drop_duplicates("Ticker", keep="last").set_index("Ticker").iterrows()
    }


def get_quality(ticker: str, is_etf: bool = False) -> FundamentalQuality:
    normalized_ticker = _ticker(ticker)
    if is_etf:
        return FundamentalQuality(
            ticker=normalized_ticker,
            quality_gate=True,
            quality_reason="ETF跳过基本面门槛",
            data_available=True,
            institution_holding_status="PASS",
            quality_data_completeness=1.0,
            quality_gate_reason="ETF跳过基本面门槛",
            quality_multiplier=QUALITY_MULTIPLIER_PASS,
        )
    path = _path_value()
    if path is None:
        return FundamentalQuality(ticker=normalized_ticker)
    row = load_fundamental_data(str(path.resolve())).get(normalized_ticker)
    if row is None:
        return FundamentalQuality(ticker=normalized_ticker)
    return calculate_quality(row, normalized_ticker)
