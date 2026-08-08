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

ETF_TRACKING_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("上证50", ("上证50",)),
    ("沪深300", ("沪深300", "HS300")),
    ("中证500", ("中证500", "ZZ500")),
    ("中证1000", ("中证1000",)),
    ("中证2000", ("中证2000",)),
    ("上证180", ("上证180",)),
    ("科创50", ("科创50",)),
    ("科创100", ("科创100",)),
    ("创业板50", ("创业板50",)),
    ("创业板", ("创业板", "创业板指")),
    ("恒生科技", ("恒生科技",)),
    ("恒生指数", ("恒生指数", "恒指")),
    ("纳斯达克100", ("纳斯达克100", "纳指100", "NASDAQ100")),
    ("标普500", ("标普500", "SP500", "S&P500")),
    ("红利", ("红利", "高股息")),
    ("证券", ("证券", "券商")),
    ("半导体", ("半导体", "芯片")),
    ("医药医疗", ("创新药", "医疗", "医药", "生物医药", "医疗器械")),
    ("黄金", ("黄金",)),
)

THEME_CLUSTER_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("医药医疗", ("化学制药", "中药", "生物制品", "医疗器械", "医疗服务", "医药", "医疗", "创新药", "生物科技")),
    ("半导体电子", ("半导体", "芯片", "集成电路", "消费电子", "电子元件", "电子")),
    ("AI算力", ("人工智能", "算力", "数据中心", "服务器", "光模块")),
    ("新能源", ("新能源", "光伏", "风电", "储能", "电池", "锂电")),
    ("资源周期", ("有色", "铜", "铝", "黄金", "煤炭", "钢铁", "化工", "稀土", "锂")),
    ("金融", ("证券", "券商", "银行", "保险")),
    ("大消费", ("消费", "白酒", "食品饮料", "家电", "零售", "旅游")),
    ("军工高端制造", ("军工", "国防", "航空", "航天", "机器人", "机械设备")),
    ("港股科技", ("恒生科技", "港股科技", "互联网")),
    ("宽基指数", ("上证50", "沪深300", "中证500", "中证1000", "中证2000", "科创50", "科创100", "创业板")),
)

_FUND_MANAGER_TOKENS = (
    "易方达", "华夏", "广发", "博时", "天弘", "南方", "嘉实", "富国", "国泰", "华泰柏瑞",
    "汇添富", "招商", "鹏华", "工银瑞信", "银华", "景顺长城", "摩根", "东财", "华安", "建信",
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


def _classification_text(*values: Any) -> str:
    return " ".join(value for value in (safe_text(item) for item in values) if value).upper()


def etf_tracking_key(
    *,
    name: Any = "",
    industry: Any = "",
    sector: Any = "",
    ticker: Any = "",
) -> str:
    """Return the underlying index/theme key, independent of ETF manager name."""
    text = _classification_text(name, industry, sector)
    for key, keywords in ETF_TRACKING_PATTERNS:
        if any(str(keyword).upper() in text for keyword in keywords):
            return key
    fallback = text
    for token in _FUND_MANAGER_TOKENS:
        fallback = fallback.replace(token.upper(), "")
    fallback = re.sub(r"ETF|LOF|基金|指数|联接|交易型开放式|发起式", "", fallback, flags=re.IGNORECASE)
    fallback = re.sub(r"[^0-9A-Z\u4e00-\u9fff]+", "", fallback).strip()
    return fallback[:24] or safe_text(ticker).upper()


def etf_theme_key(
    *,
    name: Any = "",
    industry: Any = "",
    sector: Any = "",
    ticker: Any = "",
) -> str:
    text = _classification_text(name, industry, sector)
    for theme, keywords in ETF_THEME_GROUPS:
        if any(str(keyword).upper() in text for keyword in keywords):
            return theme
    return etf_tracking_key(name=name, industry=industry, sector=sector, ticker=ticker)


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


def theme_cluster(
    *,
    is_etf: bool,
    name: Any = "",
    industry: Any = "",
    sector: Any = "",
    classification: Any = "",
    ticker: Any = "",
) -> str:
    text = _classification_text(name, industry, sector, classification)
    tracking = etf_tracking_key(name=name, industry=industry, sector=sector, ticker=ticker) if is_etf else ""
    if tracking:
        text = f"{text} {tracking}".strip()
    for cluster, keywords in THEME_CLUSTER_GROUPS:
        if any(str(keyword).upper() in text for keyword in keywords):
            return cluster
    return safe_text(classification) or safe_text(industry) or safe_text(sector) or tracking
