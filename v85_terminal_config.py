"""v85 Research Terminal presentation configuration.

展示层配置，与扫描、评分、回测逻辑隔离。
后续 GUI 与 Web Report 共用这些视觉和语义定义。
"""

from __future__ import annotations

TERMINAL_VERSION = "v85-research-terminal"

# DeepL Trading 风格研究终端配色
COLORS = {
    "background": "#F1F2F4",
    "paper": "#FFFFFF",
    "ink": "#15171A",
    "muted": "#6B7078",
    "line": "#D9DDE3",
    "red": "#E33D3D",
    "green": "#197A55",
    "amber": "#B56A13",
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
