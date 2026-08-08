from __future__ import annotations

"""Shared stock/ETF classification helpers used by ranking and exports."""

import math
import re
from typing import Any

ETF_THEME_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("医药医疗", ("创新药", "医疗", "医药", "生物科技", "生物医药", "医疗器械", "医疗设备")),
    ("半导体芯片", ("半导体", "芯片", "集成电路")),
    ("人工智能", ("人工智能", "AI", "算力", "数据中心")),
    ("机器人", ("机器人", "人形机器人")),
    ("黄金", ("黄金", "金矿")),
    ("有色金属", ("有色", "铜", "铝", "稀土", "锂")),
    ("新能源", ("新能源", "光伏", "风电", "储能", "电池")),
    ("券商", ("证券", "券商")),
    ("军工", ("军工", "国防")),
    ("消费", ("消费", "白酒", "食品饮料")),
    ("传媒游戏", ("传媒", "游戏")),
    ("港股科技", ("恒生科技", "港股科技", "互联网")),
    ("红利", ("红利", "高股息")),
)


def safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "nat", "<na>"}:
        return ""
    return text


def etf_theme_key(
    *,
    name: Any = "",
    industry: Any = "",
    sector: Any = "",
    ticker: Any = "",
) -> str:
    text = " ".join(
        value
        for value in (
            safe_text(name),
            safe_text(industry),
            safe_text(sector),
        )
        if value
    ).upper()
    for theme, keywords in ETF_THEME_GROUPS:
        if any(str(keyword).upper() in text for keyword in keywords):
            return theme
    fallback = re.sub(r"ETF|LOF|基金|指数|联接|交易型开放式", "", text, flags=re.IGNORECASE)
    fallback = re.sub(r"[^0-9A-Z\u4e00-\u9fff]+", "", fallback).strip()
    return fallback[:24] or safe_text(ticker).upper()


def model_classification(
    *,
    is_etf: bool,
    name: Any = "",
    industry: Any = "",
    sector: Any = "",
    ticker: Any = "",
) -> str:
    if is_etf:
        return etf_theme_key(name=name, industry=industry, sector=sector, ticker=ticker)
    return safe_text(industry) or safe_text(sector)
