from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config import (
    INSTITUTION_HOLDING_MIN_PERIODS,
    QUALITY_CYCLICAL_MARGIN_MAX_PERCENTILE,
    QUALITY_CYCLICAL_ROE_THRESHOLD,
    QUALITY_DEFENSIVE_ROE_THRESHOLD,
    QUALITY_FINANCIAL_ROE_THRESHOLD,
    QUALITY_GENERAL_MARGIN_MAX_PERCENTILE,
    QUALITY_GENERAL_ROE_THRESHOLD,
    QUALITY_MULTIPLIER_FAIL,
    QUALITY_MULTIPLIER_PASS,
    QUALITY_MULTIPLIER_UNKNOWN,
    QUALITY_RECOVERY_MIN_GROWTH,
    QUALITY_RESILIENT_MIN_LATEST_RATIO,
)
from fundamental_data import FUNDAMENTAL_COLUMNS, fundamental_data_path

FUNDAMENTAL_FACTOR_COLUMNS = tuple(
    column for column in FUNDAMENTAL_COLUMNS if column not in {"Ticker", "Industry"}
)


@dataclass(frozen=True)
class FundamentalQuality:
    ticker: str
    industry: str = ""
    applicable: bool = True
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
    quality_hard_data_complete: bool = False
    quality_gate_reason: str = "基本面数据缺失（中性）"
    quality_multiplier: float = QUALITY_MULTIPLIER_UNKNOWN
    quality_profile: str = "GENERAL"
    profit_trend_status: str = "UNKNOWN"
    cyclical_quality_override: bool = False

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


_FINANCIAL_INDUSTRY_KEYWORDS = ("银行", "证券", "保险", "多元金融", "信托", "金融", "租赁")
_CYCLICAL_INDUSTRY_KEYWORDS = (
    "煤炭", "工业金属", "贵金属", "小金属", "钢铁", "水泥", "建材",
    "化学原料", "化学制品", "化学纤维", "造纸", "航运", "港口", "养殖", "饲料",
)
_DEFENSIVE_INDUSTRY_KEYWORDS = (
    "电力", "燃气", "水务", "环境治理", "铁路公路", "高速公路", "公用事业",
)


def quality_profile(industry: str) -> str:
    text = str(industry or "").strip()
    if any(keyword in text for keyword in _FINANCIAL_INDUSTRY_KEYWORDS):
        return "FINANCIAL"
    if any(keyword in text for keyword in _CYCLICAL_INDUSTRY_KEYWORDS):
        return "CYCLICAL"
    if any(keyword in text for keyword in _DEFENSIVE_INDUSTRY_KEYWORDS):
        return "DEFENSIVE"
    return "GENERAL"


def profit_trend_status(y1: float, y2: float, y3: float) -> str:
    """Classify newest-to-oldest three-year profit shape without look-ahead."""
    if not all(np.isfinite(value) for value in (y1, y2, y3)):
        return "UNKNOWN"
    if y1 >= y2 >= y3:
        return "STABLE_GROWTH"
    recovery = (y1 - y2) / max(abs(y2), 1.0)
    if (
        y1 > 0
        and y1 > y2
        and y2 < y3
        and (y2 <= 0 or recovery >= QUALITY_RECOVERY_MIN_GROWTH)
    ):
        return "RECOVERY"
    if (
        y1 > 0
        and y2 > 0
        and y1 >= QUALITY_RESILIENT_MIN_LATEST_RATIO * y2
    ):
        return "RESILIENT"
    if (y1 <= 0 < y2) or (y1 < y2 <= y3):
        return "DETERIORATING"
    return "MIXED"


def _profit_strength(status: str, y1: float, y2: float, y3: float) -> float:
    if status == "UNKNOWN":
        return 0.5
    if status == "RECOVERY":
        if y2 <= 0 < y1:
            return 0.85
        recovery = (y1 - y2) / max(abs(y2), 1.0)
        return float(np.clip(0.65 + 0.25 * recovery, 0.65, 0.90))
    if status == "RESILIENT":
        ratio = y1 / y2 if y2 > 0 else 0.0
        return float(np.clip(0.55 + (ratio - 0.90), 0.55, 0.65))
    if status == "DETERIORATING":
        return 0.15
    if status == "MIXED":
        return 0.40
    growth_values: list[float] = []
    if abs(y2) > 1e-9:
        growth_values.append((y1 - y2) / abs(y2))
    if abs(y3) > 1e-9:
        growth_values.append((y2 - y3) / abs(y3))
    mean_growth = float(np.mean(growth_values)) if growth_values else 0.0
    return float(np.clip(0.5 + mean_growth / 0.50, 0.0, 1.0))


def _profile_name(profile: str) -> str:
    return {
        "FINANCIAL": "金融",
        "CYCLICAL": "周期",
        "DEFENSIVE": "防守/公用事业",
        "GENERAL": "通用严格",
    }.get(profile, profile)


def calculate_quality(row: pd.Series | dict[str, Any], ticker: str = "") -> FundamentalQuality:
    values = row.to_dict() if isinstance(row, pd.Series) else row
    normalized_ticker = _ticker(values.get("Ticker", ticker))
    industry = str(values.get("Industry", "") or "").strip()
    profile = quality_profile(industry)
    numeric = {
        column: _number(values.get(column)) for column in FUNDAMENTAL_FACTOR_COLUMNS
    }
    trend = values.get("InstitutionHoldingTrend")
    holding_status = _institution_holding_status(
        trend, numeric["InstitutionHoldingPeriods"]
    )
    roe_available = np.isfinite(numeric["ROE"])
    gross_margin_available = np.isfinite(numeric["IndustryGrossMarginPercentile"])
    profit_available = all(
        np.isfinite(numeric[column])
        for column in ("NetProfitY1", "NetProfitY2", "NetProfitY3")
    )
    holding_available = holding_status in {"PASS", "FAIL"}
    profit_status = profit_trend_status(
        numeric["NetProfitY1"], numeric["NetProfitY2"], numeric["NetProfitY3"]
    )

    margin_applicable = profile in {"GENERAL", "CYCLICAL"}
    if profile == "FINANCIAL":
        roe_threshold = QUALITY_FINANCIAL_ROE_THRESHOLD
        allowed_profit = {"STABLE_GROWTH", "RECOVERY", "RESILIENT"}
        roe_label = f"ROE>={roe_threshold:g}%"
        margin_label = ""
        profit_label = "利润趋势稳定/恢复"
    elif profile == "CYCLICAL":
        roe_threshold = QUALITY_CYCLICAL_ROE_THRESHOLD
        allowed_profit = {"STABLE_GROWTH", "RECOVERY"}
        roe_label = f"ROE>={roe_threshold:g}%"
        margin_label = f"毛利率行业前{int(QUALITY_CYCLICAL_MARGIN_MAX_PERCENTILE * 100)}%"
        profit_label = "利润趋势稳定或触底回升"
    elif profile == "DEFENSIVE":
        roe_threshold = QUALITY_DEFENSIVE_ROE_THRESHOLD
        allowed_profit = {"STABLE_GROWTH", "RESILIENT"}
        roe_label = f"ROE>={roe_threshold:g}%"
        margin_label = ""
        profit_label = "利润保持稳定"
    else:
        roe_threshold = QUALITY_GENERAL_ROE_THRESHOLD
        allowed_profit = {"STABLE_GROWTH"}
        roe_label = f"ROE>{roe_threshold:g}%"
        margin_label = f"毛利率行业前{int(QUALITY_GENERAL_MARGIN_MAX_PERCENTILE * 100)}%"
        profit_label = "近3年净利润非下降"

    roe_factor: bool | None
    if not roe_available:
        roe_factor = None
    elif profile == "GENERAL":
        roe_factor = numeric["ROE"] > roe_threshold
    else:
        roe_factor = numeric["ROE"] >= roe_threshold

    margin_factor: bool | None = None
    if margin_applicable:
        if not gross_margin_available:
            margin_factor = None
        else:
            threshold = (
                QUALITY_CYCLICAL_MARGIN_MAX_PERCENTILE
                if profile == "CYCLICAL"
                else QUALITY_GENERAL_MARGIN_MAX_PERCENTILE
            )
            margin_factor = numeric["IndustryGrossMarginPercentile"] <= threshold

    profit_factor = profit_status in allowed_profit if profit_available else None
    holding_factor = (
        True if holding_status == "PASS" else False if holding_status == "FAIL" else None
    )

    hard_factors: dict[str, bool | None] = {roe_label: roe_factor, profit_label: profit_factor}
    if margin_applicable:
        hard_factors[margin_label] = margin_factor
    hard_failed = [name for name, value in hard_factors.items() if value is False]
    hard_unknown = [name for name, value in hard_factors.items() if value is None]
    hard_data_complete = not hard_unknown

    evidence_available = [roe_available, profit_available, holding_available]
    if margin_applicable:
        evidence_available.append(gross_margin_available)
    completeness = sum(float(value) for value in evidence_available) / len(evidence_available)
    data_available = any(evidence_available)
    quality_gate = not hard_failed
    cyclical_override = bool(
        profile == "CYCLICAL"
        and profit_status == "RECOVERY"
        and profit_factor is True
        and roe_factor is not False
        and margin_factor is not False
    )

    if hard_failed:
        quality_multiplier = QUALITY_MULTIPLIER_FAIL
    elif holding_status == "FAIL" or hard_unknown or holding_status == "UNKNOWN":
        quality_multiplier = QUALITY_MULTIPLIER_UNKNOWN
    else:
        quality_multiplier = QUALITY_MULTIPLIER_PASS

    reason_parts = [f"{_profile_name(profile)}模型"]
    if hard_failed:
        reason_parts.append("硬门槛未通过：" + "、".join(hard_failed))
    elif hard_unknown:
        reason_parts.append("可用硬门槛未见失败")
    else:
        reason_parts.append("行业自适应硬门槛通过")
    if cyclical_override:
        reason_parts.append("周期利润触底回升已确认")
    if holding_status == "FAIL":
        reason_parts.append("辅助证据：机构覆盖家数未增加（不单独否决）")
    elif holding_status == "UNKNOWN":
        reason_parts.append("机构覆盖家数历史不足（中性）")
    if hard_unknown:
        reason_parts.append("数据不足：" + "、".join(hard_unknown))
    reason = "；".join(reason_parts)

    weighted_points = 0.0
    available_weight = 0.0
    if roe_available:
        roe_scale = 20.0 if profile == "GENERAL" else 15.0
        weighted_points += float(np.clip(numeric["ROE"] / roe_scale, 0.0, 1.0)) * 25.0
        available_weight += 25.0
    if margin_applicable and gross_margin_available:
        weighted_points += float(
            np.clip(1.0 - numeric["IndustryGrossMarginPercentile"], 0.0, 1.0)
        ) * 20.0
        available_weight += 20.0
    if profit_available:
        weighted_points += _profit_strength(
            profit_status,
            numeric["NetProfitY1"],
            numeric["NetProfitY2"],
            numeric["NetProfitY3"],
        ) * 25.0
        available_weight += 25.0
    if holding_available:
        weighted_points += 15.0 if holding_status == "PASS" else 0.0
        available_weight += 15.0

    if available_weight > 0:
        observed_score = weighted_points / available_weight * 100.0
        shrunk_factor_score = 50.0 + (observed_score - 50.0) * completeness
        quality_score = round(float(np.clip(shrunk_factor_score, 0.0, 100.0)), 4)
    else:
        quality_score = np.nan

    return FundamentalQuality(
        ticker=normalized_ticker,
        industry=industry,
        roe=numeric["ROE"],
        gross_margin=numeric["GrossMargin"],
        institution_holding_trend=trend,
        institution_holding_periods=numeric["InstitutionHoldingPeriods"],
        net_profit_y1=numeric["NetProfitY1"],
        net_profit_y2=numeric["NetProfitY2"],
        net_profit_y3=numeric["NetProfitY3"],
        industry_gross_margin_percentile=numeric["IndustryGrossMarginPercentile"],
        roe_factor=bool(roe_factor),
        gross_margin_factor=True if not margin_applicable else bool(margin_factor),
        institution_holding_factor=bool(holding_factor),
        net_profit_factor=bool(profit_factor),
        quality_score=quality_score,
        quality_gate=quality_gate,
        quality_reason=reason,
        data_available=data_available,
        institution_holding_status=holding_status,
        quality_data_completeness=round(completeness, 4),
        quality_hard_data_complete=hard_data_complete,
        quality_gate_reason=reason,
        quality_multiplier=quality_multiplier,
        quality_profile=profile,
        profit_trend_status=profit_status,
        cyclical_quality_override=cyclical_override,
    )


def _path_value() -> Path | None:
    return fundamental_data_path()


@lru_cache(maxsize=4)
def load_fundamental_data(path_value: str) -> dict[str, dict[str, Any]]:
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
    return frame.drop_duplicates("Ticker", keep="last").set_index("Ticker").to_dict(
        orient="index"
    )


def get_quality(ticker: str, is_etf: bool = False) -> FundamentalQuality:
    normalized_ticker = _ticker(ticker)
    if is_etf:
        return FundamentalQuality(
            ticker=normalized_ticker,
            applicable=False,
            quality_gate=True,
            quality_reason="ETF基本面门槛不适用",
            data_available=False,
            institution_holding_status="UNKNOWN",
            quality_data_completeness=0.0,
            quality_hard_data_complete=True,
            quality_gate_reason="ETF基本面门槛不适用",
            quality_multiplier=QUALITY_MULTIPLIER_PASS,
            quality_profile="ETF",
            profit_trend_status="NOT_APPLICABLE",
            cyclical_quality_override=False,
        )
    path = _path_value()
    if path is None:
        return FundamentalQuality(ticker=normalized_ticker)
    row = load_fundamental_data(str(path.resolve())).get(normalized_ticker)
    if row is None:
        return FundamentalQuality(ticker=normalized_ticker)
    return calculate_quality(row, normalized_ticker)
