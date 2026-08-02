from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fundamental_data import fundamental_data_path

FUNDAMENTAL_COLUMNS = (
    "Ticker",
    "ROE",
    "GrossMargin",
    "InstitutionHoldingTrend",
    "InstitutionHoldingPeriods",
    "NetProfitY1",
    "NetProfitY2",
    "NetProfitY3",
    "IndustryGrossMarginPercentile",
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
    quality_gate: bool = False
    quality_reason: str = "基本面数据缺失"
    data_available: bool = False

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


def calculate_quality(row: pd.Series | dict[str, Any], ticker: str = "") -> FundamentalQuality:
    values = row.to_dict() if isinstance(row, pd.Series) else row
    normalized_ticker = _ticker(values.get("Ticker", ticker))
    numeric = {column: _number(values.get(column)) for column in FUNDAMENTAL_COLUMNS[1:]}
    trend = values.get("InstitutionHoldingTrend")
    fields_present = all(
        pd.notna(values.get(column))
        for column in FUNDAMENTAL_COLUMNS[1:]
    )
    if not fields_present:
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
        )

    factors = {
        "ROE>10%": bool(np.isfinite(numeric["ROE"]) and numeric["ROE"] > 10.0),
        "毛利率行业前30%": bool(
            np.isfinite(numeric["IndustryGrossMarginPercentile"])
            and numeric["IndustryGrossMarginPercentile"] <= 0.30
        ),
        "机构持仓连续增加": bool(
            _trend_is_increasing(trend)
            and np.isfinite(numeric["InstitutionHoldingPeriods"])
            and numeric["InstitutionHoldingPeriods"] > 0
        ),
        "近3年净利润非下降": bool(
            np.isfinite(numeric["NetProfitY1"])
            and np.isfinite(numeric["NetProfitY2"])
            and np.isfinite(numeric["NetProfitY3"])
            and numeric["NetProfitY1"] >= numeric["NetProfitY2"] >= numeric["NetProfitY3"]
        ),
    }
    passed = [name for name, value in factors.items() if value]
    failed = [name for name, value in factors.items() if not value]
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
        roe_factor=factors["ROE>10%"],
        gross_margin_factor=factors["毛利率行业前30%"],
        institution_holding_factor=factors["机构持仓连续增加"],
        net_profit_factor=factors["近3年净利润非下降"],
        quality_score=round(len(passed) / 4.0 * 100.0, 4),
        quality_gate=not failed,
        quality_reason="全部通过" if not failed else "未通过：" + "、".join(failed),
        data_available=True,
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
    if not set(FUNDAMENTAL_COLUMNS).issubset(frame.columns):
        return {}
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
        )
    path = _path_value()
    if path is None:
        return FundamentalQuality(ticker=normalized_ticker)
    row = load_fundamental_data(str(path.resolve())).get(normalized_ticker)
    if row is None:
        return FundamentalQuality(ticker=normalized_ticker)
    return calculate_quality(row, normalized_ticker)
