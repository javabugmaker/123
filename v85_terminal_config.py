"""v85 Research Briefing presentation configuration.

本模块只保存 GUI 与 Web 共用的视觉/文案配置。它不导入扫描器，也不修改评分、
准入、回测或发布事务，因而可以被轻量测试和静态报告安全复用。
"""

from __future__ import annotations

TERMINAL_VERSION = "2026-08-21-v85-research-briefing-v1"
PAGE_LABEL = "A股研究简报"
BRAND_LABEL = "INSTITUTION SCANNER"

# 编辑部式研究终端配色。只吸收高密度研究简报的设计原则，不复制第三方资产。
COLORS = {
    "background": "#F1F2F4",
    "paper": "#FFFFFF",
    "ink": "#15171A",
    "muted": "#6B7078",
    "line": "#D9DDE3",
    "soft": "#EEF0F3",
    "red": "#E33D3D",
    "red_dark": "#B52B32",
    "green": "#197A55",
    "amber": "#B56A13",
    "blue": "#1769AA",
    "violet": "#6955B8",
}

TYPOGRAPHY = {
    "sans": "Microsoft YaHei UI",
    "mono": "Consolas",
}

LAYOUT = {
    "window": "1366x768",
    "minimum": (1120, 680),
    "sidebar_width": 226,
    "detail_width": 282,
    "table_row_height": 30,
}

# 首页统一信息模块顺序。
# GUI 与 Web 后续均按此顺序渲染。
HOME_SECTIONS = (
    "market_state",
    "top_opportunities",
    "sector_rotation",
    "risk_radar",
    "model_changes",
    "run_status",
)

DISPLAY_CONCEPTS = {
    "research": "研究价值",
    "execution": "交易执行",
    "risk": "风险提示",
    "trend": "趋势结构",
}

SECTION_TITLES = {
    "market_state": "MARKET STATE / 市场状态",
    "top_opportunities": "TOP OPPORTUNITIES / 今日机会",
    "sector_rotation": "SECTOR ROTATION / 行业轮动",
    "risk_radar": "RISK RADAR / 风险雷达",
    "model_changes": "MODEL CHANGES / 本轮变化",
    "run_status": "RUN STATUS / 运行状态",
}

NAV_ITEMS = (
    ("mixed", "综合"),
    ("stocks", "股票"),
    ("etf", "ETF"),
    ("ready", "可执行"),
    ("new", "新信号"),
    ("sustained", "持续"),
    ("risk", "风险"),
    ("all", "全部"),
)

EXECUTION_LABELS = {
    "READY": "可执行",
    "CAUTIOUS": "谨慎",
    "OBSERVE": "观察",
    "BLOCKED": "阻断",
    "推荐": "可执行",
    "谨慎候选": "谨慎",
    "观察": "观察",
    "风险过滤": "阻断",
}
