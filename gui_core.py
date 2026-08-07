from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from collections.abc import Mapping, Sequence
from pathlib import Path
from tkinter import messagebox, ttk

PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "output"
MAIN_FILE = PROJECT_ROOT / "main.py"
DISPLAY_VALUE_NAMES = {
    "auto": "自动优选",
    "akshare": "AkShare",
    "ACCUMULATION": "吸筹阶段",
    "BREAKOUT": "启动阶段",
    "DISTRIBUTION": "派发阶段",
    "NONE": "无明显资金行为",
    "BUY_NOW": "回调可买",
    "WAIT_PULLBACK": "等待回调",
    "BREAKOUT_CONFIRM": "突破确认买入",
    "PRICE_BREAKOUT": "价格突破待放量",
    "WAIT_VOLUME_CONFIRM": "等待量能确认",
    "HOLD_WAIT": "继续观察",
    "AVOID": "回避",
    "ETF": "ETF",
    "STOCK": "股票",
    "eastmoney": "东方财富",
    "sina": "新浪财经",
    "tencent": "腾讯财经",
    "current_survivor_pool": "当前结果股票池",
    "PASS": "通过",
    "FAIL": "未通过",
    "UNKNOWN": "数据未知（中性）",
}
DISPLAY_VALUE_CODES = {label: value for value, label in DISPLAY_VALUE_NAMES.items()}

DATA_SOURCE_CODES = {
    "自动优选": "auto",
    "AkShare": "akshare",
    "东方财富": "eastmoney",
    "新浪财经": "sina",
    "腾讯财经": "tencent",
}

DATA_SOURCE_HINTS = {
    "自动优选": "新浪优先，自动回退",
    "AkShare": "失败时自动回退",
    "东方财富": "失败时自动回退",
    "新浪财经": "失败时自动回退",
    "腾讯财经": "失败时自动回退",
}

CsvCacheToken = tuple[int, int] | tuple[int, int, str]
MISSING_VALUE_TEXTS = frozenset(
    {"", "-", "--", "na", "n/a", "nan", "none", "null", "inf", "+inf", "-inf"}
)

COLUMN_NAMES = {
    "Ticker": "代码",
    "Name": "名称",
    "Sector": "板块",
    "Industry": "行业",
    "IsETF": "类型",
    "AssetType": "类型",
    "Style": "风格",
    "Quality": "质量",
    "OverallRank": "综合排名",
    "RankingScore": "交易排序分",
    "RankingEligibility": "排序资格",
    "TradeReadinessReason": "执行资格说明",
    "RankingReason": "排序原因",
    "InstitutionalScore": "机构评分",
    "InstitutionalTier": "机构等级",
    "InstitutionalPercentile": "机构分位",
    "InstitutionalRank": "机构排名",
    "InstitutionalTierReason": "等级原因",
    "Close": "收盘价",
    "Score": "基础评分",
    "BaseScore": "基础质量分",
    "TriggerScore": "启动买点分",
    "FinalScore": "最终评分",
    "BreakoutScore": "启动概率",
    "SmartMoneyStage": "资金阶段",
    "EntryScore": "买点评分",
    "EntrySignal": "买点信号",
    "EntryZone": "买入区间",
    "BreakoutBuyPrice": "突破买入价",
    "BreakoutVolumeRatio": "突破量比",
    "BreakoutVolumeConfirmed": "突破量能确认",
    "BreakoutFlowConfirmed": "突破资金确认",
    "PriceBreakout": "价格突破",
    "StopLoss": "止损位",
    "ValueTrapRisk": "价值陷阱风险",
    "ChaseRiskScore": "追高风险",
    "ChaseRiskLevel": "追高风险等级",
    "ChaseRiskReason": "追高风险原因",
    "HardRiskFlag": "硬风险标记",
    "HardRiskPenalty": "硬风险系数",
    "HardRiskReason": "硬风险原因",
    "RankingPenaltyReason": "排序降权原因",
    "RiskWarning": "风险提示",
    "OperationAdvice": "操作建议",
    "BacktestScore": "回测评分",
    "CompositeScore": "综合回测评分",
    "BacktestSamples": "回测样本数",
    "BacktestEffectiveSamples": "有效回测样本",
    "BacktestReliability": "回测可靠度",
    "BacktestEffectiveWeight": "回测有效权重",
    "BacktestConfidenceTier": "回测可信度等级",
    "BacktestAdjustedScore": "回测收缩评分",
    "BacktestWinRate20D": "20日胜率",
    "BacktestWinRate60D": "60日胜率",
    "BacktestAverageReturn20D": "20日平均收益",
    "BacktestAverageReturn60D": "60日平均收益",
    "BacktestObjectiveValue": "回测目标值",
    "UniverseType": "股票池类型",
    "SurvivorshipBiasWarning": "幸存者偏差警告",
    "TrendScore": "趋势分",
    "VolumeScore": "成交量分",
    "AccumulationScore": "吸筹分",
    "CompressionScore": "波动分",
    "StructureScore": "结构分",
    "OBV": "能量潮指标",
    "CMF": "资金流量指标",
    "AD": "累积派发指标",
    "ATR14": "平均真实波幅",
    "MA20": "20日均线",
    "MA50": "50日均线",
    "MA200": "200日均线",
    "RSI14": "RSI14",
    "DistToLow52W": "距52周低点",
    "WyckoffPhase": "威科夫阶段",
    "Stage": "阶段",
    "MarketRegime": "市场环境",
    "MarketRegimeFast": "快线市场环境",
    "MarketRegimeSlow": "慢线市场环境",
    "MarketRegimeConfidence": "市场环境置信度",
    "MarketRegimeReason": "市场环境原因",
    "IndustryRelativeStrength": "行业强度",
    "DataAsOf": "数据日期",
    "DataAgeDays": "自然日延迟",
    "DataTradingAgeDays": "交易日延迟",
    "DataCoverage": "数据覆盖率",
    "DataFreshnessStatus": "行情时效",
    "DataFreshnessFactor": "行情时效系数",
    "DataFreshnessReason": "行情时效说明",
    "InstitutionHoldingStatus": "机构持仓状态",
    "QualityDataCompleteness": "质量数据完整度",
    "QualityGateReason": "质量门槛原因",
    "QualityMultiplier": "质量系数",
    "SignalAdjustmentReason": "信号调整原因",
    "OpportunityStage": "机会阶段",
    "VolAccumDays": "放量天数",
    "ShortTermScore": "短期机会分",
    "MediumTermScore": "中期机会分",
    "LongTermScore": "长期机会分",
    "OpportunityScore": "机会评分",
    "LifecycleStage": "生命周期阶段",
    "ActionSuggestion": "操作建议",
    "RiskNote": "风险提示",
    "SignalDays": "连续信号天数",
    "SignalStartDate": "信号起始日",
    "SignalStatus": "信号状态",
    "SignalStrengthHistory": "信号强度历史",
    "SignalTrend": "信号趋势",
    "ScoreConfidencePct": "评分置信度",
    "ScoreCoverage": "指标覆盖率",
    "ScoreConfidence": "评分置信度",
    "ScoreContributionTrend": "趋势贡献",
    "ScoreContributionVolume": "成交量贡献",
    "ScoreContributionAccumulation": "吸筹贡献",
    "ScoreContributionCompression": "波动贡献",
    "ScoreContributionStructure": "结构贡献",
    "SignalCount": "信号数",
    "FilterCount": "通过项数",
    "PassedFilters": "基础筛选",
    "OBV_Div": "OBV背离",
    "CMF_Pos": "CMF为正或改善",
    "CMF_Improving": "CMF改善",
    "AD_SlopePos": "A/D上升",
    "BearMarket": "熊市条件",
    "Consolidation": "横盘整理",
    "VolAccum": "放量吸筹",
    "VolContract": "波动收缩",
    "Error": "错误",
}
DISPLAY_COLUMNS = (
    "OverallRank",
    "Ticker",
    "Name",
    "AssetType",
    "EntrySignal",
    "BreakoutVolumeRatio",
    "RankingEligibility",
    "PassedFilters",
    "TradeReadinessReason",
    "RankingScore",
    "InstitutionalTier",
    "InstitutionalScore",
    "FinalScore",
    "QualityGate",
    "QualityDataCompleteness",
    "BacktestSamples",
    "BacktestConfidenceTier",
    "ValueTrapRisk",
    "ChaseRiskScore",
    "HardRiskFlag",
    "DataFreshnessStatus",
    "MarketRegime",
    "DataAsOf",
    "RankingReason",
)
COLUMN_WIDTHS = {
    "Ticker": 96,
    "Name": 120,
    "AssetType": 58,
    "Sector": 86,
    "Industry": 98,
    "Quality": 72,
    "OverallRank": 72,
    "RankingScore": 88,
    "RankingEligibility": 82,
    "TradeReadinessReason": 200,
    "InstitutionalTier": 96,
    "InstitutionalScore": 86,
    "Score": 72,
    "BaseScore": 78,
    "TriggerScore": 92,
    "FinalScore": 78,
    "BreakoutScore": 78,
    "SmartMoneyStage": 92,
    "EntrySignal": 112,
    "BreakoutVolumeRatio": 82,
    "EntryZone": 112,
    "StopLoss": 78,
    "ValueTrapRisk": 92,
    "ChaseRiskScore": 82,
    "HardRiskFlag": 82,
    "MarketRegime": 92,
    "OpportunityScore": 82,
    "LifecycleStage": 96,
    "SignalTrend": 86,
    "SignalDays": 94,
    "BacktestScore": 82,
    "CompositeScore": 94,
    "BacktestReliability": 82,
    "BacktestSamples": 82,
    "BacktestConfidenceTier": 96,
    "BacktestEffectiveSamples": 96,
    "Close": 72,
    "DistToLow52W": 92,
    "WyckoffPhase": 118,
    "Stage": 88,
    "SignalCount": 68,
    "PassedFilters": 70,
    "QualityGate": 78,
    "QualityDataCompleteness": 96,
    "DataFreshnessStatus": 82,
    "DataFreshnessFactor": 96,
    "DataFreshnessReason": 190,
    "DataAsOf": 92,
    "RankingReason": 220,
    "SignalAdjustmentReason": 220,
}
NUMBER_COLUMNS = {
    "Score",
    "OverallRank",
    "RankingScore",
    "InstitutionalScore",
    "InstitutionalPercentile",
    "InstitutionalRank",
    "BaseScore",
    "TriggerScore",
    "FinalScore",
    "BreakoutScore",
    "EntryScore",
    "BreakoutVolumeRatio",
    "StopLoss",
    "ValueTrapRisk",
    "ChaseRiskScore",
    "HardRiskPenalty",
    "QualityDataCompleteness",
    "QualityMultiplier",
    "BreakoutBuyPrice",
    "BacktestScore",
    "CompositeScore",
    "BacktestObjectiveValue",
    "BacktestWinRate20D",
    "BacktestWinRate60D",
    "ScoreConfidence",
    "ScoreMissingIndicators",
    "BacktestSamples",
    "BacktestEffectiveSamples",
    "BacktestReliability",
    "BacktestEffectiveWeight",
    "BacktestAdjustedScore",
    "Close",
    "DistToLow52W",
    "VolAccumDays",
    "SignalCount",
    "SignalDays",
    "OpportunityScore",
    "ShortTermScore",
    "MediumTermScore",
    "LongTermScore",
    "ScoreCoverage",
    "ScoreConfidencePct",
    "ScoreContributionTrend",
    "ScoreContributionVolume",
    "ScoreContributionAccumulation",
    "ScoreContributionCompression",
    "ScoreContributionStructure",
    "MarketRegimeConfidence",
    "DataFreshnessFactor",
}
TEXT_COLUMNS = {
    "Name",
    "Sector",
    "Industry",
    "WyckoffPhase",
    "Stage",
    "LifecycleStage",
    "SignalTrend",
    "SmartMoneyStage",
    "EntrySignal",
    "EntryZone",
    "RiskWarning",
    "OperationAdvice",
    "RankingEligibility",
    "TradeReadinessReason",
    "RankingReason",
    "InstitutionalTier",
    "InstitutionalTierReason",
    "InstitutionHoldingStatus",
    "QualityGateReason",
    "SignalAdjustmentReason",
    "BacktestConfidenceTier",
    "ChaseRiskLevel",
    "ChaseRiskReason",
    "HardRiskReason",
    "RankingPenaltyReason",
}
INTEGER_COLUMNS = {
    "ScoreMissingIndicators",
    "BacktestSamples",
    "OverallRank",
    "InstitutionalRank",
    "VolAccumDays",
    "SignalCount",
    "SignalDays",
}
PERCENTAGE_COLUMNS = {
    "DistToLow52W",
    "ScoreCoverage",
    "ScoreConfidence",
    "ScoreConfidencePct",
    "BacktestWinRate20D",
    "BacktestWinRate60D",
    "BacktestReliability",
    "BacktestEffectiveWeight",
    "QualityDataCompleteness",
    "MarketRegimeConfidence",
    "DataFreshnessFactor",
}
FRACTION_PERCENTAGE_COLUMNS = {
    "ScoreCoverage",
    "ScoreConfidence",
    "BacktestWinRate20D",
    "BacktestWinRate60D",
    "BacktestReliability",
    "BacktestEffectiveWeight",
    "QualityDataCompleteness",
    "MarketRegimeConfidence",
    "DataFreshnessFactor",
}
FOUR_DECIMAL_COLUMNS = {"BacktestObjectiveValue"}
MAX_RENDERED_ROWS = 500
DOWNLOAD_PROGRESS_RE = re.compile(
    r"DOWNLOAD progress: (\d+)/(\d+) \((\d+) succeeded, (\d+) no-data/failed\)\."
)
FUNDAMENTAL_PROGRESS_RE = re.compile(
    r"FUNDAMENTAL progress: (\d+)/(\d+) \((\d+) updated, (\d+) unavailable\)\."
)
ANALYSE_PROGRESS_RE = re.compile(
    r"ANALYSE progress: (\d+)/(\d+) \((\d+) successful, (\d+) failed\)\."
)


class ScannerGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("A股机构吸筹扫描器")
        self.root.geometry("1440x900")
        self.root.minsize(1100, 650)
        self.process: subprocess.Popen[str] | None = None
        self.scan_running = False
        self.backtest_running = False
        self._cancel_requested = False
        self._closing = False
        self.scope = tk.StringVar(value="全部股票和ETF")
        self.tickers = tk.StringVar()
        self.search = tk.StringVar()
        self.sector_filter = tk.StringVar(value="全部板块")
        self.industry_filter = tk.StringVar(value="全部行业")
        self.quality_filter = tk.StringVar(value="全部质量")
        self.stage_filter = tk.StringVar(value="全部阶段")
        self.entry_filter = tk.StringVar(value="全部买点")
        self.eligibility_filter = tk.StringVar(value="全部资格")
        self.no_resume = tk.BooleanVar(value=False)
        self.force_download = tk.BooleanVar(value=False)
        self.cache_first = tk.BooleanVar(value=False)
        self.refresh_fundamentals = tk.BooleanVar(value=False)
        self.data_source = tk.StringVar(value="自动优选")
        self.data_source_label = tk.StringVar(
            value="当前：自动优选 · 新浪优先，自动回退"
        )
        self.status = tk.StringVar(value="就绪")
        self.result_summary = tk.StringVar(value="等待加载结果")
        self._row_details: dict[str, dict[str, str]] = {}
        self.filtered_tickers: list[str] = []
        self._csv_headers: list[str] = []
        self._csv_rows: list[list[str]] = []
        self._csv_indexes: dict[str, int] = {}
        self._csv_search_text: list[str] = []
        self._display_headers: list[str] = []
        self._display_indexes: list[int] = []
        self._table_headers: tuple[str, ...] = ()
        self._csv_path: Path | None = None
        # A two-part token is retained as a supported legacy state for old
        # sessions/tests.  Freshly loaded files always use the three-part
        # token, which also carries a content hash.
        self._csv_mtime: CsvCacheToken | None = None
        self._filter_job: str | None = None
        self._sort_column: str | None = "RankingScore"
        self._sort_descending = True
        self._current_page = 0
        self.page_summary = tk.StringVar(value="")
        self._log_queue: queue.Queue[str] = queue.Queue()
        self._log_job = self.root.after(150, self._flush_log_queue)
        self._configure_style()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.search.trace_add("write", self._schedule_filter_refresh)
        self.sector_filter.trace_add("write", self._schedule_filter_refresh)
        self.industry_filter.trace_add("write", self._schedule_filter_refresh)
        self.quality_filter.trace_add("write", self._schedule_filter_refresh)
        self.stage_filter.trace_add("write", self._schedule_filter_refresh)
        self.entry_filter.trace_add("write", self._schedule_filter_refresh)
        self.eligibility_filter.trace_add("write", self._schedule_filter_refresh)
        self.root.bind("<Control-f>", lambda _event: self._focus_search())
        self.root.bind("<Escape>", lambda _event: self.clear_filters())
        self.root.bind("<F5>", lambda _event: self.refresh_results())
        if hasattr(self, "search_entry"):
            self.search_entry.bind("<Return>", self._render_search_results)
        self._load_best_available_results()

    def _configure_style(self) -> None:
        self.root.configure(background="#f4f7fb")
        self.root.option_add("*Font", ("Microsoft YaHei UI", 9))
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#f4f7fb")
        style.configure("TLabel", background="#f4f7fb", foreground="#243b53")
        style.configure("TLabelframe", background="#f4f7fb", bordercolor="#d7e2ee")
        style.configure(
            "TLabelframe.Label",
            background="#f4f7fb",
            foreground="#17324d",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.configure("Header.TFrame", background="#17324d")
        style.configure(
            "Title.TLabel",
            background="#17324d",
            foreground="white",
            font=("Microsoft YaHei UI", 18, "bold"),
        )
        style.configure(
            "Sub.TLabel",
            background="#17324d",
            foreground="#cbd9e8",
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "HeaderStatus.TLabel",
            background="#244864",
            foreground="#d9ebff",
            padding=(12, 6),
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.configure("Quiet.TButton", padding=(10, 6))
        style.configure("Toolbar.TFrame", background="#ffffff")
        style.configure("Filter.TFrame", background="#eaf2fb")
        style.configure(
            "ResultTitle.TLabel",
            background="#f4f7fb",
            foreground="#17324d",
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        style.configure(
            "Treeview",
            rowheight=26,
            font=("Microsoft YaHei UI", 9),
            background="white",
            fieldbackground="white",
            foreground="#243b53",
        )
        style.map(
            "Treeview",
            background=[("selected", "#dbeafe")],
            foreground=[("selected", "#17324d")],
        )
        style.configure(
            "Treeview.Heading",
            font=("Microsoft YaHei UI", 9, "bold"),
            background="#eaf2fb",
            foreground="#17324d",
            padding=(8, 7),
        )
        style.configure("TCombobox", padding=4)
        style.configure("TEntry", padding=4)
        style.configure(
            "Accent.TButton",
            foreground="white",
            background="#1677ff",
            padding=(18, 9),
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.map("Accent.TButton", background=[("active", "#4096ff")])
        style.configure(
            "Status.TLabel",
            background="#f4f7fb",
            foreground="#55708a",
            font=("Microsoft YaHei UI", 9),
        )

    def _build_ui(self) -> None:
        header = ttk.Frame(self.root, style="Header.TFrame", padding=(24, 16))
        header.pack(fill=tk.X)
        header.grid_columnconfigure(0, weight=1)
        title_frame = ttk.Frame(header, style="Header.TFrame")
        title_frame.grid(row=0, column=0, sticky=tk.W)
        ttk.Label(title_frame, text="A股机构吸筹扫描器", style="Title.TLabel").pack(
            anchor=tk.W
        )
        ttk.Label(
            title_frame, text="全市场股票与 ETF · 技术指标 · 评分筛选", style="Sub.TLabel"
        ).pack(anchor=tk.W, pady=(4, 0))
        ttk.Label(header, textvariable=self.status, style="HeaderStatus.TLabel").grid(
            row=0, column=1, sticky=tk.E, padx=(20, 0)
        )

        controls = ttk.LabelFrame(self.root, text="扫描设置", padding=12)
        controls.pack(fill=tk.X, padx=18, pady=(14, 8))
        controls.columnconfigure(3, weight=1)
        controls.columnconfigure(4, weight=1)
        controls.columnconfigure(7, weight=1)
        ttk.Label(controls, text="扫描范围").grid(
            row=0, column=0, padx=(0, 6), sticky=tk.W
        )
        box = ttk.Combobox(
            controls,
            textvariable=self.scope,
            values=("全部股票和ETF", "仅股票", "仅ETF"),
            state="readonly",
            width=18,
        )
        box.grid(row=0, column=1, padx=(0, 20), sticky=tk.W)
        ttk.Label(controls, text="指定代码").grid(
            row=0, column=2, padx=(0, 6), sticky=tk.W
        )
        ttk.Entry(controls, textvariable=self.tickers, width=38).grid(
            row=0, column=3, padx=(0, 8), sticky=tk.EW
        )
        ttk.Label(controls, text="例：588000.SH,000001.SZ", foreground="#708399").grid(
            row=0, column=4, sticky=tk.W
        )
        ttk.Label(controls, text="数据源").grid(
            row=0, column=5, padx=(12, 4), sticky=tk.W
        )
        self.source_box = ttk.Combobox(
            controls,
            textvariable=self.data_source,
            values=tuple(DATA_SOURCE_CODES),
            state="readonly",
            width=12,
        )
        self.source_box.grid(row=0, column=6, sticky=tk.W)
        self.source_box.bind("<<ComboboxSelected>>", self._data_source_changed)
        ttk.Label(
            controls, textvariable=self.data_source_label, foreground="#55708a"
        ).grid(row=0, column=7, padx=(8, 0), sticky=tk.W)
        ttk.Checkbutton(controls, text="不使用断点", variable=self.no_resume).grid(
            row=1, column=0, columnspan=2, pady=(12, 0), sticky=tk.W
        )
        ttk.Checkbutton(
            controls, text="强制重新下载", variable=self.force_download
        ).grid(row=1, column=2, columnspan=2, pady=(12, 0), sticky=tk.W)
        ttk.Checkbutton(
            controls, text="快速扫描（优先缓存）", variable=self.cache_first
        ).grid(row=1, column=4, pady=(12, 0), sticky=tk.W)
        ttk.Checkbutton(
            controls, text="刷新基本面数据", variable=self.refresh_fundamentals
        ).grid(row=1, column=5, pady=(12, 0), sticky=tk.W)
        self.start_button = ttk.Button(
            controls, text="▶ 开始扫描", style="Accent.TButton", command=self.start_scan
        )
        self.start_button.grid(row=1, column=6, pady=(10, 0), sticky=tk.E)
        self.cancel_button = ttk.Button(
            controls, text="取消运行", command=self.cancel_running_task, state=tk.DISABLED
        )
        self.cancel_button.grid(row=1, column=7, padx=(8, 0), pady=(10, 0), sticky=tk.W)

        actions = ttk.Frame(self.root, style="Toolbar.TFrame", padding=(14, 8))
        actions.pack(fill=tk.X, padx=18, pady=(0, 2))
        for text, command in (
            ("生成前50名", self._load_top50), ("交易就绪", self._load_trade_ready),
            ("启动候选", lambda: self.load_csv("Top50BreakoutCandidates.csv")),
            ("买点候选", lambda: self.load_csv("Top50EntryCandidates.csv")),
            ("风险榜", lambda: self.load_csv("Top50ValueTrapRisk.csv")),
            ("市场概览", self.show_market_overview),
            ("连续信号", self.show_sustained_signals), ("全部结果", lambda: self.load_csv("AllResults.csv")),
            ("结果目录", self.open_output), ("运行回测", self.start_backtest), ("查看回测", self.show_backtest),
        ):
            ttk.Button(actions, text=text, style="Quiet.TButton", command=command).pack(side=tk.LEFT, padx=(0, 6))
        self.progress = ttk.Progressbar(actions, mode="indeterminate", length=180)
        self.progress.pack(side=tk.RIGHT, padx=(10, 0))
        ttk.Label(actions, textvariable=self.status, style="Status.TLabel").pack(side=tk.RIGHT)

        filters = ttk.Frame(self.root, style="Filter.TFrame", padding=(14, 8))
        filters.pack(fill=tk.X, padx=18, pady=(0, 4))
        ttk.Label(filters, text="板块", padding=(0, 0, 4, 0)).pack(side=tk.LEFT)
        self.sector_box = ttk.Combobox(filters, textvariable=self.sector_filter, state="readonly", width=12)
        self.sector_box.pack(side=tk.LEFT)
        self.sector_box.bind("<<ComboboxSelected>>", self._sector_changed)
        ttk.Label(filters, text="行业", padding=(12, 0, 4, 0)).pack(side=tk.LEFT)
        self.industry_box = ttk.Combobox(filters, textvariable=self.industry_filter, state="readonly", width=14)
        self.industry_box.pack(side=tk.LEFT)
        ttk.Label(filters, text="质量", padding=(12, 0, 4, 0)).pack(side=tk.LEFT)
        ttk.Combobox(filters, textvariable=self.quality_filter, values=("全部质量", "强候选", "候选", "观察", "普通"), state="readonly", width=9).pack(side=tk.LEFT)
        ttk.Label(filters, text="资金阶段", padding=(12, 0, 4, 0)).pack(side=tk.LEFT)
        self.stage_box = ttk.Combobox(filters, textvariable=self.stage_filter, state="readonly", width=12)
        self.stage_box.pack(side=tk.LEFT)
        ttk.Label(filters, text="买点", padding=(12, 0, 4, 0)).pack(side=tk.LEFT)
        self.entry_box = ttk.Combobox(filters, textvariable=self.entry_filter, state="readonly", width=15)
        self.entry_box.pack(side=tk.LEFT)
        ttk.Label(filters, text="资格", padding=(12, 0, 4, 0)).pack(side=tk.LEFT)
        self.eligibility_box = ttk.Combobox(
            filters,
            textvariable=self.eligibility_filter,
            values=("全部资格", "推荐", "观察", "风险过滤"),
            state="readonly",
            width=9,
        )
        self.eligibility_box.pack(side=tk.LEFT)
        ttk.Label(filters, text="搜索", padding=(12, 0, 4, 0)).pack(side=tk.LEFT)
        self.search_entry = ttk.Entry(filters, textvariable=self.search, width=24)
        self.search_entry.pack(side=tk.LEFT)
        ttk.Button(filters, text="清空筛选", command=self.clear_filters).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(filters, text="刷新结果", command=self.refresh_results).pack(side=tk.LEFT, padx=(6, 0))
        self.market_overview = tk.StringVar(value="市场概览：等待结果")
        ttk.Label(filters, textvariable=self.market_overview, style="Status.TLabel", padding=(16, 0, 0, 0)).pack(side=tk.LEFT)

        body = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        body.pack(fill=tk.BOTH, expand=True, padx=18, pady=(6, 16))
        table_frame = ttk.Frame(body)
        result_bar = ttk.Frame(table_frame, padding=(0, 0, 0, 6))
        result_bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(result_bar, text="扫描结果", style="ResultTitle.TLabel").pack(side=tk.LEFT)
        ttk.Label(result_bar, textvariable=self.result_summary, style="Status.TLabel", padding=(10, 0, 0, 0)).pack(side=tk.LEFT)
        ttk.Label(result_bar, text="双击行查看完整详情 · 单击表头排序", style="Status.TLabel").pack(side=tk.RIGHT)
        pagination = ttk.Frame(table_frame, padding=(0, 0, 0, 6))
        pagination.grid(row=1, column=0, columnspan=2, sticky=tk.E)
        self.previous_page_button = ttk.Button(
            pagination, text="上一页", command=self._show_previous_page
        )
        self.previous_page_button.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Label(pagination, textvariable=self.page_summary, style="Status.TLabel").pack(
            side=tk.LEFT
        )
        self.next_page_button = ttk.Button(
            pagination, text="下一页", command=self._show_next_page
        )
        self.next_page_button.pack(side=tk.LEFT, padx=(6, 0))
        self.table = ttk.Treeview(table_frame, show="headings", selectmode="browse")
        self.table.tag_configure("quality-strong", background="#e8f7ee", foreground="#17663a")
        self.table.tag_configure("quality-candidate", background="#eef6ff", foreground="#1f5f9c")
        self.table.tag_configure("quality-watch", background="#fff8e6", foreground="#8a5a00")
        self.table.tag_configure("quality-normal", background="#f5f6f8", foreground="#596575")
        self.table.tag_configure("entry-buy", background="#e3f7e8", foreground="#12623a")
        self.table.tag_configure("entry-breakout", background="#e4f1ff", foreground="#165d9b")
        self.table.tag_configure("entry-pullback", background="#f4efff", foreground="#6541a5")
        self.table.tag_configure("entry-price", background="#fff7df", foreground="#8a5a00")
        self.table.tag_configure("entry-hold", background="#f5f6f8", foreground="#596575")
        self.table.tag_configure("entry-avoid", background="#ffe8e8", foreground="#a22222")
        self.table.tag_configure("risk-filter", background="#ffe8e8", foreground="#a22222")
        self.table.tag_configure("data-stale", background="#fff2df", foreground="#9a5300")
        self.table.tag_configure("quality-fail", background="#fff5e8", foreground="#8a5a00")
        ybar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.table.yview)
        xbar = ttk.Scrollbar(
            table_frame, orient=tk.HORIZONTAL, command=self.table.xview
        )
        self.table.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.table.bind("<Double-1>", self.show_selected_detail)
        self.table.grid(row=2, column=0, sticky="nsew")
        ybar.grid(row=2, column=1, sticky="ns")
        xbar.grid(row=3, column=0, sticky="ew")
        table_frame.rowconfigure(2, weight=1)
        table_frame.columnconfigure(0, weight=1)
        body.add(table_frame, weight=5)
        log_frame = ttk.LabelFrame(body, text="运行日志", padding=6)
        self.log_text = tk.Text(
            log_frame,
            height=8,
            wrap=tk.NONE,
            state=tk.DISABLED,
            bg="#17212b",
            fg="#d5e4f2",
            insertbackground="white",
            font=("Consolas", 9),
        )
        logbar = ttk.Scrollbar(
            log_frame, orient=tk.VERTICAL, command=self.log_text.yview
        )
        self.log_text.configure(yscrollcommand=logbar.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        logbar.pack(side=tk.RIGHT, fill=tk.Y)
        ttk.Button(log_frame, text="清空日志", command=self.clear_log).pack(
            side=tk.RIGHT, padx=(6, 0), pady=(0, 4), anchor=tk.N
        )
        body.add(log_frame, weight=2)

    def build_command(self) -> list[str]:
        command = [sys.executable, str(MAIN_FILE), "scan"]
        if self.tickers.get().strip():
            command += ["--tickers", self.tickers.get().strip()]
        if self.scope.get() == "仅股票":
            command.append("--stocks-only")
        elif self.scope.get() == "仅ETF":
            command.append("--etfs-only")
        if self.no_resume.get() or self.force_download.get():
            command.append("--no-resume")
        if self.force_download.get():
            command.append("--force-download")
        cache_first = getattr(self, "cache_first", None)
        if cache_first is not None and cache_first.get() and not self.force_download.get():
            command.append("--cache-first")
        refresh_fundamentals = getattr(self, "refresh_fundamentals", None)
        if refresh_fundamentals is not None and refresh_fundamentals.get():
            command.append("--refresh-fundamentals")
        command += ["--data-source", self._selected_data_source()]
        return command

    def _selected_data_source(self) -> str:
        return DATA_SOURCE_CODES.get(self.data_source.get(), "auto")

    def _top50_tickers(self) -> list[str]:
        path = OUTPUT_DIR / "Top50.csv"
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as file:
                reader = csv.DictReader(file)
                return [
                    ticker.strip().upper()
                    for row in list(reader)[:50]
                    if (ticker := str(row.get("Ticker", ""))).strip()
                ]
        except (OSError, UnicodeError, csv.Error):
            return []

    def _atomic_write_text(
        self, path: Path, content: str, encoding: str = "utf-8"
    ) -> None:
        temporary_path = path.with_name(f".{path.name}.tmp")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            temporary_path.write_text(content, encoding=encoding)
            os.replace(temporary_path, path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    @staticmethod
    def _cell_text(value: object) -> str:
        return "" if value is None else str(value).strip()

    @classmethod
    def _numeric_value(cls, value: object) -> float | None:
        text = cls._cell_text(value).replace(",", "").rstrip("%")
        if not text:
            return None
        try:
            number = float(text)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @classmethod
    def _is_missing_text(cls, value: object) -> bool:
        return cls._cell_text(value).casefold() in MISSING_VALUE_TEXTS

    def _write_top50_csv(self, tickers: list[str]) -> Path:
        path = OUTPUT_DIR / "Top50.csv"
        if "Ticker" not in self._csv_headers:
            raise ValueError("当前结果缺少 Ticker 列，无法生成 Top50.csv")
        ordered_tickers = list(
            dict.fromkeys(
                ticker.strip().upper() for ticker in tickers if ticker.strip()
            )
        )[:50]
        ticker_index = self._csv_headers.index("Ticker")
        rows_by_ticker = {
            self._cell_text(row[ticker_index]).upper(): row
            for row in self._csv_rows
            if len(row) > ticker_index and self._cell_text(row[ticker_index])
        }
        selected = [
            rows_by_ticker[ticker]
            for ticker in ordered_tickers
            if ticker in rows_by_ticker
        ]
        if len(selected) != len(ordered_tickers):
            raise ValueError("当前筛选结果与表格数据不一致，无法生成 Top50.csv")
        temporary_path = path.with_name(f".{path.name}.tmp")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with temporary_path.open("w", encoding="utf-8-sig", newline="") as file:
                csv.writer(file).writerows([self._csv_headers, *selected])
            os.replace(temporary_path, path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
        self._csv_path = None
        self._csv_mtime = None
        return path

    def _load_top50(self) -> None:
        if not self._csv_headers or not self.filtered_tickers:
            messagebox.showinfo(
                "提示", "当前筛选结果为空，请先完成扫描或调整筛选条件。"
            )
            return
        tickers = list(dict.fromkeys(self.filtered_tickers))[:50]
        try:
            self._write_top50_csv(tickers)
            if not self.load_csv("Top50.csv"):
                raise ValueError("Top50.csv 已生成，但未包含有效结果")
        except (OSError, UnicodeError, csv.Error, ValueError) as exc:
            messagebox.showerror("生成 Top50 失败", str(exc))
            return
        self.append_log(f"已从当前筛选结果生成 Top50.csv：{len(tickers)} 只\n")

    def _load_trade_ready(self) -> None:
        filename = "Top50TradeReady.csv"
        if self._csv_has_results(filename):
            self.load_csv(filename)
            return
        messagebox.showinfo("交易就绪", "当前结果中没有满足即时交易条件的标的。")

    def start_backtest(self) -> None:
        if self.scan_running:
            messagebox.showinfo("提示", "当前任务正在运行中")
            return
        backtest_tickers = list(dict.fromkeys(self.filtered_tickers))
        if not backtest_tickers:
            messagebox.showerror(
                "无法运行回测",
                "当前筛选结果为空，请先完成扫描或调整筛选条件。",
            )
            return
        ticker_file = OUTPUT_DIR / "BacktestAll.txt"
        try:
            self._atomic_write_text(ticker_file, "\n".join(backtest_tickers) + "\n")
        except (OSError, UnicodeError, csv.Error, ValueError) as exc:
            messagebox.showerror("准备回测失败", str(exc))
            return
        self.scan_running = True
        self.backtest_running = True
        self._cancel_requested = False
        self.start_button.configure(state=tk.DISABLED)
        if hasattr(self, "cancel_button"):
            self.cancel_button.configure(state=tk.NORMAL)
        self.progress.start(12)
        command = [
            sys.executable,
            str(MAIN_FILE),
            "backtest",
            "--data-source",
            self._selected_data_source(),
            "--tickers-file",
            str(ticker_file),
        ]
        self.append_log(f"回测当前筛选结果：{len(backtest_tickers)} 个标的\n")
        self.append_log(
            f"执行回测命令：{MAIN_FILE.name} backtest --数据源 {self.data_source.get()} --股票列表 BacktestAll.txt\n"
        )
        threading.Thread(target=self.run_process, args=(command,), daemon=True).start()

    def show_backtest(self) -> None:
        path = OUTPUT_DIR / "BacktestSummary.json"
        if not path.exists():
            messagebox.showinfo("回测结果", "尚未生成回测结果")
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            dialog = tk.Toplevel(self.root)
            dialog.title("历史回测结果")
            dialog.geometry("760x680")
            dialog.minsize(620, 480)
            dialog.configure(background="#f4f7fb")
            ttk.Label(
                dialog, text="历史回测结果", font=("Microsoft YaHei UI", 16, "bold")
            ).pack(anchor=tk.W, padx=22, pady=(20, 4))
            ttk.Label(
                dialog,
                text="统计本次回测传入的股票集合，不代表全市场表现",
                foreground="#55708a",
            ).pack(anchor=tk.W, padx=22, pady=(0, 12))
            frame = ttk.Frame(dialog, padding=(20, 4))
            frame.pack(fill=tk.BOTH, expand=True)
            text = tk.Text(
                frame,
                wrap=tk.WORD,
                state=tk.DISABLED,
                bg="white",
                fg="#243b53",
                relief=tk.FLAT,
                padx=14,
                pady=14,
                font=("Microsoft YaHei UI", 10),
            )
            scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
            text.configure(yscrollcommand=scroll.set)
            text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scroll.pack(side=tk.RIGHT, fill=tk.Y)
            ticker_count = data.get("ticker_count", len(data.get("by_ticker", [])))
            lines = [
                f"样本数：{data.get('samples', 0)}",
                f"股票数：{ticker_count}",
                f"20日胜率：{float(data.get('win_rate_20d', 0)) * 100:.2f}%",
                f"20日平均收益：{float(data.get('average_return_20d', 0)):.2f}%",
                f"20日中位数收益：{float(data.get('median_return_20d', 0)):.2f}%",
                f"60日胜率：{float(data.get('win_rate_60d', 0)) * 100:.2f}%",
                f"60日平均收益：{float(data.get('average_return_60d', 0)):.2f}%",
                f"60日中位数收益：{float(data.get('median_return_60d', 0)):.2f}%",
                f"回测目标值：{float(data.get('objective_value', 0)):.4f}",
                f"股票池类型：{data.get('universe_type', 'current_survivor_pool')}",
                f"幸存者偏差警告：{data.get('survivorship_bias_warning', True)}",
                "",
                "说明：胜率为未来收益大于 0 的样本占比，收益率单位为百分比。",
            ]
            text.configure(state=tk.NORMAL)
            text.insert("1.0", "\n".join(lines))
            text.configure(state=tk.DISABLED)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, tk.TclError) as exc:
            messagebox.showerror("读取回测结果失败", str(exc))

    def start_scan(self) -> None:
        if self.scan_running:
            messagebox.showinfo("提示", "扫描正在运行中")
            return
        self.clear_log()
        self.scan_running = True
        self._cancel_requested = False
        self._csv_path = None
        self._csv_mtime = None
        self.scan_output_mtime = self._results_mtime()
        self.start_button.configure(state=tk.DISABLED)
        self.cancel_button.configure(state=tk.NORMAL)
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=100, value=0)
        self.status.set("准备扫描")
        command = self.build_command()
        self.append_log("执行：" + " ".join(command) + "\n")
        threading.Thread(target=self.run_process, args=(command,), daemon=True).start()

    def run_process(self, command: list[str]) -> None:
        try:
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            self.process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
            )
            if self._cancel_requested:
                self.process.terminate()
            assert self.process.stdout is not None
            for line in self.process.stdout:
                self._log_queue.put(line)
            code = self.process.wait()
            self.process = None
            self.root.after(0, self.scan_finished, code)
        except (OSError, subprocess.SubprocessError, tk.TclError) as exc:
            self.root.after(0, self.scan_failed, str(exc))

    def _flush_log_queue(self) -> None:
        if self._closing:
            return
        lines: list[str] = []
        while len(lines) < 200:
            try:
                lines.append(self._log_queue.get_nowait())
            except queue.Empty:
                break
        if lines:
            latest_fundamental_progress = None
            latest_download_progress = None
            latest_analyse_progress = None
            rendered_lines: list[str] = []
            for line in lines:
                if FUNDAMENTAL_PROGRESS_RE.search(line):
                    latest_fundamental_progress = line
                elif DOWNLOAD_PROGRESS_RE.search(line):
                    latest_download_progress = line
                elif ANALYSE_PROGRESS_RE.search(line):
                    latest_analyse_progress = line
                else:
                    rendered_lines.append(line)
            if latest_fundamental_progress:
                rendered_lines.append(latest_fundamental_progress)
            if latest_download_progress:
                rendered_lines.append(latest_download_progress)
            if latest_analyse_progress:
                rendered_lines.append(latest_analyse_progress)
            self.append_log("".join(rendered_lines))
        self._log_job = self.root.after(150, self._flush_log_queue)

    def scan_finished(self, code: int) -> None:
        self.progress.stop()
        was_backtest = self.backtest_running
        was_cancelled = self._cancel_requested
        self.scan_running = False
        self.process = None
        self.start_button.configure(state=tk.NORMAL)
        if hasattr(self, "cancel_button"):
            self.cancel_button.configure(state=tk.DISABLED)
        self.backtest_running = False
        self._cancel_requested = False
        if self._closing:
            self._shutdown()
            return
        if was_cancelled:
            self.status.set("任务已取消")
            self.append_log("任务已取消。\n")
            return
        self.status.set("扫描完成" if code == 0 else f"任务结束，退出码：{code}")
        if (
            code == 0
            and was_backtest
            and (OUTPUT_DIR / "BacktestSummary.json").exists()
        ):
            self.show_backtest()
            self._load_best_available_results()
        elif code == 0:
            if not self._load_best_available_results():
                if self._results_mtime() == getattr(self, "scan_output_mtime", ()):
                    self.status.set("扫描完成，但结果文件未更新")
                    self.append_log(
                        "扫描进程已完成，但没有找到有效结果文件，请检查运行日志。\n"
                    )
                else:
                    self.status.set("扫描完成，但结果文件为空")
                    self.append_log("扫描完成，但结果文件没有有效数据。\n")
        else:
            self.append_log("本次扫描失败，结果文件未刷新。\n")

    def _csv_has_results(self, filename: str) -> bool:
        path = OUTPUT_DIR / filename
        if not path.exists() or path.stat().st_size <= 3:
            return False
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as file:
                reader = csv.reader(file)
                headers = next(reader, [])
                if "Ticker" not in headers:
                    return False
                ticker_index = headers.index("Ticker")
                return any(
                    len(row) > ticker_index and row[ticker_index].strip()
                    for row in reader
                )
        except (OSError, UnicodeError, csv.Error):
            return False

    def _load_best_available_results(self) -> bool:
        for filename in ("Top50.csv", "AllResults.csv"):
            if self._csv_has_results(filename):
                return self.load_csv(filename)
        return False

    def scan_failed(self, error: str) -> None:
        self.progress.stop()
        self.scan_running = False
        self.process = None
        self.start_button.configure(state=tk.NORMAL)
        if hasattr(self, "cancel_button"):
            self.cancel_button.configure(state=tk.DISABLED)
        self.backtest_running = False
        self._cancel_requested = False
        if self._closing:
            self._shutdown()
            return
        self.status.set("扫描启动失败")
        self.append_log(error + "\n")
        messagebox.showerror("运行失败", error)

    def cancel_running_task(self) -> None:
        if not self.scan_running:
            messagebox.showinfo("提示", "当前没有可取消的任务")
            return
        if not messagebox.askyesno("取消运行", "确定要取消当前任务吗？"):
            return
        self._cancel_process()

    def _cancel_process(self) -> None:
        self._cancel_requested = True
        if hasattr(self, "cancel_button"):
            self.cancel_button.configure(state=tk.DISABLED)
        self.status.set("正在取消任务")
        try:
            if self.process is not None:
                self.process.terminate()
        except OSError as exc:
            self.append_log(f"取消任务失败：{exc}\n")

    def on_close(self) -> None:
        if not self.scan_running:
            self._shutdown()
            return
        if not messagebox.askyesno("退出程序", "任务正在运行，是否取消任务并退出？"):
            return
        self._closing = True
        self._cancel_process()
        self._close_when_stopped()

    def _close_when_stopped(self) -> None:
        if self.scan_running:
            self.root.after(100, self._close_when_stopped)
            return
        self._shutdown()

    def _shutdown(self) -> None:
        if self._filter_job is not None:
            self.root.after_cancel(self._filter_job)
            self._filter_job = None
        if self._log_job is not None:
            self.root.after_cancel(self._log_job)
            self._log_job = None
        self.root.destroy()

    def append_log(self, text: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)
        fundamental_progress = FUNDAMENTAL_PROGRESS_RE.search(text)
        progress = DOWNLOAD_PROGRESS_RE.search(text)
        analyse_progress = ANALYSE_PROGRESS_RE.search(text)
        if fundamental_progress:
            completed, total, updated, unavailable = (
                int(value) for value in fundamental_progress.groups()
            )
            self.progress.stop()
            self.progress.configure(
                mode="determinate", maximum=max(total, 1), value=completed
            )
            self.status.set(
                f"基本面进度 {completed}/{total} · 已更新 {updated} · 暂不可用 {unavailable}"
            )
        elif progress:
            completed, total, successful, skipped = (
                int(value) for value in progress.groups()
            )
            self.progress.stop()
            self.progress.configure(
                mode="determinate", maximum=max(total, 1), value=completed
            )
            self.status.set(
                f"下载进度 {completed}/{total} · 成功 {successful} · 无数据/失败 {skipped}"
            )
        elif analyse_progress:
            completed, total, successful, failed = (
                int(value) for value in analyse_progress.groups()
            )
            self.progress.stop()
            self.progress.configure(
                mode="determinate", maximum=max(total, 1), value=completed
            )
            self.status.set(
                f"指标分析 {completed}/{total} · 成功 {successful} · 失败 {failed}"
            )
        elif "Phase 2/2:" in text:
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
            self.status.set("正在生成评分与买点")
        elif "扫描" in text and "完成" in text:
            self.status.set("扫描完成")
        elif text.strip() and not self.backtest_running:
            self.status.set("扫描运行中")

    def show_selected_detail(self, _event=None) -> None:
        selection = self.table.selection()
        if not selection:
            return
        values = self.table.item(selection[0], "values")
        data = self._row_details.get(selection[0], {})
        if not data:
            headers = list(self.table["columns"])
            data = dict(zip(headers, values))
        dialog = tk.Toplevel(self.root)
        dialog.title(f"标的详情 · {data.get('Ticker', '')}")
        dialog.geometry("620x620")
        dialog.minsize(520, 420)
        dialog.configure(background="#f4f7fb")
        ttk.Label(
            dialog,
            text=f"{data.get('Ticker', '')}  {data.get('Name', '')}",
            font=("Microsoft YaHei UI", 16, "bold"),
        ).pack(anchor=tk.W, padx=22, pady=(20, 4))
        ttk.Label(
            dialog,
            text=(
                f"资金阶段：{data.get('SmartMoneyStage', 'NONE')}  ·  "
                f"买点：{data.get('EntrySignal', 'AVOID')}  ·  "
                f"最终评分：{data.get('FinalScore', data.get('Score', ''))}"
            ),
            foreground="#55708a",
        ).pack(anchor=tk.W, padx=22, pady=(0, 12))
        frame = ttk.Frame(dialog, padding=(20, 4))
        frame.pack(fill=tk.BOTH, expand=True)
        text = tk.Text(
            frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg="white",
            fg="#243b53",
            relief=tk.FLAT,
            padx=14,
            pady=14,
            font=("Microsoft YaHei UI", 10),
        )
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        detail_keys = [
            "OverallRank",
            "RankingScore",
            "RankingEligibility",
            "TradeReadinessReason",
            "RankingReason",
            "RankingPenaltyReason",
            "InstitutionalTier",
            "InstitutionalScore",
            "InstitutionalPercentile",
            "InstitutionalRank",
            "InstitutionalTierReason",
            "Quality",
            "QualityGate",
            "QualityDataCompleteness",
            "QualityGateReason",
            "InstitutionHoldingStatus",
            "Score",
            "OpportunityScore",
            "ShortTermScore",
            "MediumTermScore",
            "LongTermScore",
            "LifecycleStage",
            "SignalTrend",
            "SignalStatus",
            "SignalDays",
            "SignalStartDate",
            "ActionSuggestion",
            "RiskNote",
            "ScoreCoverage",
            "ScoreConfidence",
            "ScoreConfidencePct",
            "ScoreMissingIndicators",
            "ScoreContributionTrend",
            "ScoreContributionVolume",
            "ScoreContributionAccumulation",
            "ScoreContributionCompression",
            "ScoreContributionStructure",
            "BaseScore",
            "TriggerScore",
            "FinalScore",
            "BreakoutScore",
            "SmartMoneyStage",
            "EntryScore",
            "EntrySignal",
            "EntryZone",
            "BreakoutBuyPrice",
            "BreakoutVolumeRatio",
            "BreakoutVolumeConfirmed",
            "BreakoutFlowConfirmed",
            "PriceBreakout",
            "StopLoss",
            "ValueTrapRisk",
            "RiskWarning",
            "OperationAdvice",
            "BacktestScore",
            "CompositeScore",
            "BacktestObjectiveValue",
            "BacktestSamples",
            "BacktestEffectiveSamples",
            "BacktestReliability",
            "BacktestEffectiveWeight",
            "BacktestConfidenceTier",
            "BacktestAdjustedScore",
            "BacktestWinRate20D",
            "BacktestWinRate60D",
            "BacktestAverageReturn20D",
            "BacktestAverageReturn60D",
            "BacktestMedianReturn20D",
            "BacktestMedianReturn60D",
            "BacktestMaxDrawdown20D",
            "BacktestMaxDrawdown60D",
            "BacktestProfitFactor",
            "BacktestSignalSpanDays",
            "UniverseType",
            "SurvivorshipBiasWarning",
            "TrendScore",
            "VolumeScore",
            "AccumulationScore",
            "CompressionScore",
            "StructureScore",
            "WyckoffPhase",
            "IndustryRelativeStrength",
            "DataSource",
            "DataAsOf",
            "DataAgeDays",
            "DataTradingAgeDays",
            "DataCoverage",
            "DataFreshnessStatus",
            "DataFreshnessFactor",
            "DataFreshnessReason",
            "MarketRegime",
            "MarketRegimeFast",
            "MarketRegimeSlow",
            "MarketRegimeConfidence",
            "MarketRegimeReason",
            "ChaseRiskScore",
            "ChaseRiskLevel",
            "ChaseRiskReason",
            "HardRiskFlag",
            "HardRiskPenalty",
            "HardRiskReason",
            "SignalAdjustmentReason",
            "SignalCount",
            "FilterCount",
            "PassedFilters",
            "OBV_Div",
            "CMF_Pos",
            "CMF_Improving",
            "AD_SlopePos",
            "BearMarket",
            "Consolidation",
            "VolAccum",
            "VolContract",
            "Error",
        ]
        lines = [
            f"{COLUMN_NAMES.get(key, key)}：{self._format_table_value(key, data.get(key, ''))}"
            for key in detail_keys
            if data.get(key, "") not in ("", None)
        ]
        text.configure(state=tk.NORMAL)
        text.insert("1.0", "\n".join(lines))
        text.configure(state=tk.DISABLED)

    def _results_mtime(self) -> tuple[tuple[str, int], ...]:
        files = ("Top50.csv", "AllResults.csv", "Top200.parquet", "AllResults.parquet")
        return tuple(
            (name, (OUTPUT_DIR / name).stat().st_mtime_ns)
            for name in files
            if (OUTPUT_DIR / name).exists()
        )

    def _focus_search(self):
        if not hasattr(self, "search_entry"):
            return "break"
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, tk.END)
        return "break"

    def clear_log(self) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def clear_filters(self) -> None:
        self.search.set("")
        self.sector_filter.set("全部板块")
        self.industry_filter.set("全部行业")
        self.quality_filter.set("全部质量")
        if hasattr(self, "stage_filter"):
            self.stage_filter.set("全部阶段")
        if hasattr(self, "entry_filter"):
            self.entry_filter.set("全部买点")
        if hasattr(self, "eligibility_filter"):
            self.eligibility_filter.set("全部资格")
        self._current_page = 0
        if self._filter_job is not None:
            self.root.after_cancel(self._filter_job)
            self._filter_job = None
        self._render_cached_rows()

    def _data_source_changed(self, _event=None) -> None:
        label = self.data_source.get() or "自动优选"
        hint = DATA_SOURCE_HINTS.get(label, "自动回退")
        self.data_source_label.set(f"当前：{label} · {hint}")
        self.status.set(f"已切换数据源：{label}")

    def _sector_changed(self, _event=None) -> None:
        self.industry_filter.set("全部行业")
        self._update_filter_values(self._csv_headers, self._csv_rows)

    def _update_filter_values(self, headers: list[str], rows: list[list[str]]) -> None:
        def values_for(column: str) -> list[str]:
            if column not in headers:
                return []
            index = headers.index(column)
            return sorted(
                {
                    self._cell_text(row[index])
                    for row in rows
                    if len(row) > index and self._cell_text(row[index])
                }
            )

        sectors = values_for("Sector")
        self.sector_box["values"] = ["全部板块", *sectors]
        if self.sector_filter.get() not in self.sector_box["values"]:
            self.sector_filter.set("全部板块")

        if hasattr(self, "stage_box"):
            stages = [DISPLAY_VALUE_NAMES.get(value, value) for value in values_for("SmartMoneyStage")]
            self.stage_box["values"] = ["全部阶段", *stages]
            if self.stage_filter.get() not in self.stage_box["values"]:
                self.stage_filter.set("全部阶段")
        if hasattr(self, "entry_box"):
            entries = [DISPLAY_VALUE_NAMES.get(value, value) for value in values_for("EntrySignal")]
            self.entry_box["values"] = ["全部买点", *entries]
            if self.entry_filter.get() not in self.entry_box["values"]:
                self.entry_filter.set("全部买点")
        if hasattr(self, "eligibility_box"):
            eligibility = values_for("RankingEligibility")
            self.eligibility_box["values"] = ["全部资格", *eligibility]
            if self.eligibility_filter.get() not in self.eligibility_box["values"]:
                self.eligibility_filter.set("全部资格")

        industries = values_for("Industry")
        if self.sector_filter.get() != "全部板块" and "Sector" in headers:
            sector_index = headers.index("Sector")
            industry_index = headers.index("Industry") if "Industry" in headers else -1
            industries = sorted(
                {
                    self._cell_text(row[industry_index])
                    for row in rows
                    if industry_index >= 0
                    and len(row) > max(sector_index, industry_index)
                    and self._cell_text(row[sector_index]) == self.sector_filter.get()
                    and self._cell_text(row[industry_index])
                }
            )
        self.industry_box["values"] = ["全部行业", *industries]
        if self.industry_filter.get() not in self.industry_box["values"]:
            self.industry_filter.set("全部行业")

    def _schedule_filter_refresh(self, *_args) -> None:
        self._current_page = 0
        if self._filter_job is not None:
            self.root.after_cancel(self._filter_job)
        self._filter_job = self.root.after(180, self._render_cached_rows)

    def _render_search_results(self, _event=None):
        self._current_page = 0
        self._render_cached_rows()
        return "break"

    def refresh_results(self) -> bool:
        filename = getattr(self, "current_file", "")
        if self._filter_job is not None:
            self.root.after_cancel(self._filter_job)
            self._filter_job = None
        self._current_page = 0
        if not filename:
            return self._load_best_available_results()
        self._csv_path = None
        self._csv_mtime = None
        loaded = self.load_csv(filename)
        if loaded:
            self.status.set(f"已刷新：{filename}")
        return loaded

    def _clear_result_view(self) -> None:
        self._csv_headers = []
        self._csv_rows = []
        self._csv_indexes = {}
        self._csv_search_text = []
        self._display_headers = []
        self._display_indexes = []
        self._table_headers = ()
        self._csv_path = None
        self._csv_mtime = None
        self.filtered_tickers = []
        self._current_page = 0
        if hasattr(self, "_row_details"):
            self._row_details.clear()
        if hasattr(self, "table"):
            self.table.delete(*self.table.get_children())
            self.table["columns"] = ()
        if hasattr(self, "sector_box"):
            self._update_filter_values([], [])
        if hasattr(self, "page_summary"):
            self.page_summary.set("")
        if hasattr(self, "previous_page_button"):
            self.previous_page_button.configure(state=tk.DISABLED)
        if hasattr(self, "next_page_button"):
            self.next_page_button.configure(state=tk.DISABLED)
        if hasattr(self, "result_summary"):
            self.result_summary.set("等待加载结果")

    def _row_matches_filters(
        self,
        indexes: dict[str, int],
        row: list[str],
        query: str,
        search_text: str | None = None,
        filter_values: tuple[str, str, str, str, str, str] | None = None,
    ) -> bool:
        values = (
            row
            if len(row) >= len(self._csv_headers)
            else row + [""] * (len(self._csv_headers) - len(row))
        )

        def value_for(column: str) -> str:
            index = indexes.get(column)
            return self._cell_text(values[index]) if index is not None and index < len(values) else ""

        if filter_values is None:
            filter_values = (
                self.sector_filter.get(),
                self.industry_filter.get(),
                self.quality_filter.get(),
                self.stage_filter.get() if hasattr(self, "stage_filter") else "全部阶段",
                self.entry_filter.get() if hasattr(self, "entry_filter") else "全部买点",
                self.eligibility_filter.get() if hasattr(self, "eligibility_filter") else "全部资格",
            )
        (
            sector_value,
            industry_value,
            quality_value,
            stage_value,
            entry_value,
            eligibility_value,
        ) = filter_values
        searchable = (
            search_text
            if search_text is not None
            else " ".join(map(self._cell_text, values)).casefold()
        )
        return (
            (not query or query in searchable)
            and (
                sector_value == "全部板块" or value_for("Sector") == sector_value
            )
            and (
                industry_value == "全部行业"
                or value_for("Industry") == industry_value
            )
            and (
                quality_value == "全部质量" or value_for("Quality") == quality_value
            )
            and (
                stage_value == "全部阶段"
                or value_for("SmartMoneyStage")
                == DISPLAY_VALUE_CODES.get(stage_value, stage_value)
            )
            and (
                entry_value == "全部买点"
                or value_for("EntrySignal")
                == DISPLAY_VALUE_CODES.get(entry_value, entry_value)
            )
            and (
                eligibility_value == "全部资格"
                or value_for("RankingEligibility") == eligibility_value
            )
        )

    def _market_overview_values(
        self, rows: list[list[str]], indexes: dict[str, int]
    ) -> tuple[int, int, int, int, int, float]:
        def number_for(row: list[str], column: str) -> float:
            index = indexes.get(column)
            if index is None or index >= len(row):
                return 0.0
            return self._numeric_value(row[index]) or 0.0

        total = len(rows)
        active = sum(number_for(row, "SignalDays") > 0 for row in rows)
        lifecycle_index = indexes.get("LifecycleStage")
        confirmed = sum(
            len(row) > lifecycle_index
            and self._cell_text(row[lifecycle_index]) == "趋势确认"
            for row in rows
            if lifecycle_index is not None
        )
        breakout_index = indexes.get("SmartMoneyStage")
        entry_index = indexes.get("EntrySignal")
        breakout = sum(
            self._cell_text(row[breakout_index]) == "BREAKOUT"
            for row in rows
            if breakout_index is not None and len(row) > breakout_index
        )
        eligibility_index = indexes.get("RankingEligibility")
        if eligibility_index is not None:
            actionable = sum(
                len(row) > eligibility_index
                and self._cell_text(row[eligibility_index]) == "推荐"
                for row in rows
            )
        else:
            actionable = sum(
                self._cell_text(row[entry_index])
                in {"BUY_NOW", "BREAKOUT_CONFIRM", "WAIT_PULLBACK"}
                for row in rows
                if entry_index is not None and len(row) > entry_index
            )
        average = (
            sum(number_for(row, "FinalScore") for row in rows) / total
            if total and "FinalScore" in indexes
            else sum(number_for(row, "OpportunityScore") for row in rows) / total
            if total and "OpportunityScore" in indexes
            else 0.0
        )
        return total, active, confirmed, breakout, actionable, average

    def _update_market_overview(
        self, rows: list[list[str]], indexes: dict[str, int]
    ) -> None:
        if not hasattr(self, "market_overview"):
            return
        total, active, confirmed, breakout, actionable, average = self._market_overview_values(rows, indexes)
        regime = (
            f" · {self._market_regime_summary(rows, indexes)}"
            if "MarketRegime" in indexes
            else ""
        )
        self.market_overview.set(
            f"市场概览：{total} 只 · 启动 {breakout} · 可交易 {actionable}{regime} · 最终均分 {average:.1f}"
        )

    def _market_regime_summary(
        self, rows: list[list[str]], indexes: dict[str, int]
    ) -> str:
        regime_index = indexes.get("MarketRegime")
        if regime_index is None:
            return "市场环境未知"
        values = [
            self._cell_text(row[regime_index])
            for row in rows
            if regime_index < len(row) and self._cell_text(row[regime_index])
        ]
        if not values:
            return "市场环境未知"
        counts: dict[str, int] = {}
        for value in values:
            counts[value] = counts.get(value, 0) + 1
        regime = max(counts, key=lambda value: counts[value])
        confidence_index = indexes.get("MarketRegimeConfidence")
        confidences: list[float] = []
        if confidence_index is not None:
            for row in rows:
                if regime_index >= len(row) or confidence_index >= len(row):
                    continue
                if self._cell_text(row[regime_index]) != regime:
                    continue
                confidence = self._numeric_value(row[confidence_index])
                if confidence is not None:
                    confidences.append(confidence)
        if confidences:
            average_confidence = sum(confidences) / len(confidences)
            if average_confidence <= 1.0:
                average_confidence *= 100.0
            return f"市场 {regime}（{average_confidence:.0f}%）"
        return f"市场 {regime}"

    def show_market_overview(self) -> None:
        if not self._csv_headers:
            messagebox.showinfo("市场概览", "请先完成扫描或加载结果文件。")
            return
        indexes = {header: index for index, header in enumerate(self._csv_headers)}
        query = self.search.get().strip().casefold()
        filter_values = (
            self.sector_filter.get(),
            self.industry_filter.get(),
            self.quality_filter.get(),
            self.stage_filter.get() if hasattr(self, "stage_filter") else "全部阶段",
            self.entry_filter.get() if hasattr(self, "entry_filter") else "全部买点",
            self.eligibility_filter.get() if hasattr(self, "eligibility_filter") else "全部资格",
        )
        search_texts = getattr(self, "_csv_search_text", [])
        if len(search_texts) != len(self._csv_rows):
            search_texts = [
                " ".join(map(self._cell_text, row)).casefold()
                for row in self._csv_rows
            ]
        rows = [
            row
            for row, search_text in zip(self._csv_rows, search_texts)
            if self._row_matches_filters(
                indexes, row, query, search_text, filter_values
            )
        ]
        total, active, confirmed, breakout, actionable, average = self._market_overview_values(rows, indexes)
        dialog = tk.Toplevel(self.root)
        dialog.title("市场概览")
        dialog.geometry("520x360")
        dialog.minsize(460, 300)
        dialog.configure(background="#f4f7fb")
        ttk.Label(
            dialog, text="市场概览", font=("Microsoft YaHei UI", 16, "bold")
        ).pack(anchor=tk.W, padx=22, pady=(20, 4))
        ttk.Label(
            dialog,
            text=f"基于当前筛选条件 · {getattr(self, 'current_file', '结果文件')}",
            foreground="#55708a",
        ).pack(anchor=tk.W, padx=22, pady=(0, 12))
        lines = [
            f"标的数量：{total}",
            f"活跃信号：{active}",
            f"趋势确认：{confirmed}",
            f"启动阶段：{breakout}",
            f"可交易信号：{actionable}",
            self._market_regime_summary(rows, indexes),
            f"平均最终评分：{average:.1f}",
        ]
        text = tk.Text(
            dialog,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg="white",
            fg="#243b53",
            relief=tk.FLAT,
            padx=16,
            pady=14,
            font=("Microsoft YaHei UI", 10),
        )
        text.pack(fill=tk.BOTH, expand=True, padx=22, pady=(0, 20))
        text.configure(state=tk.NORMAL)
        text.insert("1.0", "\n".join(lines))
        text.configure(state=tk.DISABLED)

    def show_sustained_signals(self) -> None:
        for filename in ("Top50SustainedSignals.csv", "SignalTracking.csv"):
            if self._csv_has_results(filename):
                self.load_csv(filename)
                return
        messagebox.showinfo("连续信号榜", "请先完成扫描，生成连续信号榜后再查看。")

    def _format_table_value(self, column: str, value: str) -> str:
        text = self._cell_text(value)
        if column in {"SmartMoneyStage", "EntrySignal", "AssetType", "DataSource", "UniverseType"}:
            return DISPLAY_VALUE_NAMES.get(text, text)
        if column in {"QualityGate", "PassedFilters"}:
            return self._format_boolean_status(text)
        if column == "HardRiskFlag":
            if self._is_missing_text(text):
                return "未知"
            return "是" if text.lower() in {"true", "1", "yes", "y", "是"} else "否"
        if column not in NUMBER_COLUMNS or not text:
            return text
        number = self._numeric_value(text)
        if number is None:
            return "—" if self._is_missing_text(text) else text
        if column in INTEGER_COLUMNS:
            return f"{number:,.0f}"
        if column in PERCENTAGE_COLUMNS:
            percent = number * 100 if column in FRACTION_PERCENTAGE_COLUMNS else number
            return f"{percent:.2f}%" if column == "DistToLow52W" else f"{percent:.0f}%"
        return f"{number:,.2f}"

    def _format_boolean_status(self, value: object) -> str:
        text = self._cell_text(value)
        if self._is_missing_text(text):
            return "未知"
        return "通过" if text.lower() in {"true", "1", "yes", "y", "是", "通过"} else "未通过"

    def _sort_value(
        self, column: str, row: Sequence[object], indexes: Mapping[str, int]
    ) -> tuple[bool, str | float]:
        index = indexes[column]
        value = self._cell_text(row[index]) if len(row) > index else ""
        if column not in NUMBER_COLUMNS:
            return (not value, value.casefold())
        number = self._numeric_value(value)
        return (number is None, number if number is not None else 0.0)

    def _quality_tag(self, quality: str) -> str:
        tags = {
            "强候选": "quality-strong",
            "候选": "quality-candidate",
            "观察": "quality-watch",
        }
        return tags.get(quality.strip(), "quality-normal")

    def _entry_tag(self, signal: str) -> str:
        signal = signal.strip().upper()
        if signal == "BUY_NOW":
            return "entry-buy"
        if signal == "BREAKOUT_CONFIRM":
            return "entry-breakout"
        if signal == "WAIT_PULLBACK":
            return "entry-pullback"
        if signal in {"PRICE_BREAKOUT", "WAIT_VOLUME_CONFIRM"}:
            return "entry-price"
        if signal == "AVOID":
            return "entry-avoid"
        return "entry-hold"

    def _risk_tag(self, values: list[str], indexes: dict[str, int]) -> str:
        def value_for(column: str) -> str:
            index = indexes.get(column)
            return self._cell_text(values[index]) if index is not None and index < len(values) else ""

        if value_for("RankingEligibility") == "风险过滤":
            return "risk-filter"
        if value_for("DataFreshnessStatus") == "过期":
            return "data-stale"
        if value_for("QualityGate").strip().lower() in {"false", "0", "no", "否"}:
            return "quality-fail"
        return ""

    def _sort_by_column(self, column: str) -> None:
        if self._sort_column == column:
            self._sort_descending = not self._sort_descending
        else:
            self._sort_column = column
            self._sort_descending = column in NUMBER_COLUMNS
        self._current_page = 0
        self._render_cached_rows()

    def _show_previous_page(self) -> None:
        if self._current_page > 0:
            self._current_page -= 1
            self._render_cached_rows()

    def _show_next_page(self) -> None:
        self._current_page += 1
        self._render_cached_rows()

    def _render_cached_rows(self) -> bool:
        self._filter_job = None
        if not hasattr(self, "_current_page"):
            self._current_page = 0
        headers = self._csv_headers
        data_rows = self._csv_rows
        if not headers:
            return False
        indexes = getattr(self, "_csv_indexes", {})
        if len(indexes) != len(headers) or any(
            indexes.get(header) != index for index, header in enumerate(headers)
        ):
            indexes = {header: index for index, header in enumerate(headers)}
        self._csv_indexes = indexes
        query = self.search.get().strip().casefold()
        filter_values = (
            self.sector_filter.get(),
            self.industry_filter.get(),
            self.quality_filter.get(),
            self.stage_filter.get() if hasattr(self, "stage_filter") else "全部阶段",
            self.entry_filter.get() if hasattr(self, "entry_filter") else "全部买点",
            self.eligibility_filter.get() if hasattr(self, "eligibility_filter") else "全部资格",
        )
        search_texts = getattr(self, "_csv_search_text", [])
        if len(search_texts) != len(data_rows):
            search_texts = [
                " ".join(map(self._cell_text, row)).casefold() for row in data_rows
            ]
        self._csv_search_text = search_texts
        filtered = [
            row
            for row, search_text in zip(data_rows, search_texts)
            if self._row_matches_filters(
                indexes, row, query, search_text, filter_values
            )
        ]
        ticker_index = indexes.get("Ticker", -1)
        display_headers = getattr(self, "_display_headers", [])
        if not display_headers or any(column not in headers for column in display_headers):
            display_headers = [column for column in DISPLAY_COLUMNS if column in headers]
        self._display_headers = display_headers
        sort_column = getattr(self, "_sort_column", None)
        sort_descending = getattr(self, "_sort_descending", True)
        if isinstance(sort_column, str) and sort_column in indexes:
            sortable_rows = [
                (self._sort_value(sort_column, row, indexes), position, row)
                for position, row in enumerate(filtered)
            ]
            sortable_rows.sort(key=lambda item: item[0][1], reverse=sort_descending)
            sortable_rows.sort(key=lambda item: item[0][0])
            filtered = [item[2] for item in sortable_rows]
        self.filtered_tickers = [
            self._cell_text(row[ticker_index]).upper()
            for row in filtered
            if ticker_index >= 0
            and len(row) > ticker_index
            and self._cell_text(row[ticker_index])
        ]
        self._update_market_overview(filtered, indexes)
        self.table.delete(*self.table.get_children())
        self._row_details.clear()
        self.table["columns"] = display_headers
        table_headers = tuple(display_headers)
        headers_changed = getattr(self, "_table_headers", ()) != table_headers
        for header in display_headers:
            anchor = (
                tk.E
                if header in NUMBER_COLUMNS
                else tk.W
                if header in TEXT_COLUMNS
                else tk.CENTER
            )
            arrow = (
                " ▼"
                if sort_column == header and sort_descending
                else " ▲"
                if sort_column == header
                else ""
            )
            self.table.heading(
                header,
                text=f"{COLUMN_NAMES.get(header, header)}{arrow}",
                command=lambda column=header: self._sort_by_column(column),
            )
            if headers_changed:
                self.table.column(
                    header,
                    width=COLUMN_WIDTHS.get(header, 90),
                    anchor=anchor,
                    stretch=False,
                )
        self._table_headers = table_headers
        header_indexes = getattr(self, "_display_indexes", [])
        if len(header_indexes) != len(display_headers) or any(
            index != indexes[column]
            for column, index in zip(display_headers, header_indexes)
        ):
            header_indexes = [indexes[column] for column in display_headers]
        self._display_indexes = header_indexes
        quality_index = indexes.get("Quality")
        entry_signal_index = indexes.get("EntrySignal")
        asset_type_display_index = (
            display_headers.index("AssetType") if "AssetType" in display_headers else None
        )
        passed_filters_display_index = (
            display_headers.index("PassedFilters")
            if "PassedFilters" in display_headers
            else None
        )
        page_count = max(1, (len(filtered) + MAX_RENDERED_ROWS - 1) // MAX_RENDERED_ROWS)
        self._current_page = min(self._current_page, page_count - 1)
        start_index = self._current_page * MAX_RENDERED_ROWS
        page_rows = filtered[start_index : start_index + MAX_RENDERED_ROWS]
        rendered_count = len(page_rows)
        for row in page_rows:
            values = row + [""] * max(0, len(headers) - len(row))
            display_values = [
                self._format_table_value(header, values[index])
                for header, index in zip(display_headers, header_indexes)
            ]
            if asset_type_display_index is not None:
                display_values[asset_type_display_index] = (
                    "ETF"
                    if str(display_values[asset_type_display_index]).strip().lower() == "etf"
                    else "股票"
                )
            if passed_filters_display_index is not None:
                display_values[passed_filters_display_index] = self._format_boolean_status(
                    values[header_indexes[passed_filters_display_index]]
                )
            quality = values[quality_index] if quality_index is not None else ""
            entry_signal = values[entry_signal_index] if entry_signal_index is not None else ""
            risk_tag = self._risk_tag(values, indexes)
            item_id = self.table.insert(
                "", tk.END, values=display_values,
                tags=tuple(
                    tag
                    for tag in (
                        risk_tag,
                        self._entry_tag(entry_signal),
                        self._quality_tag(quality),
                    )
                    if tag
                ),
            )
            self._row_details[item_id] = dict(zip(headers, values))
        if hasattr(self, "page_summary"):
            self.page_summary.set(
                f"第 {self._current_page + 1} / {page_count} 页 · {start_index + 1 if filtered else 0}-{start_index + rendered_count} 条"
            )
        if hasattr(self, "previous_page_button"):
            self.previous_page_button.configure(
                state=tk.NORMAL if self._current_page > 0 else tk.DISABLED
            )
        if hasattr(self, "next_page_button"):
            self.next_page_button.configure(
                state=tk.NORMAL if self._current_page + 1 < page_count else tk.DISABLED
            )
        if hasattr(self, "result_summary"):
            eligibility_index = indexes.get("RankingEligibility")
            freshness_index = indexes.get("DataFreshnessStatus")
            recommended = sum(
                len(row) > eligibility_index
                and self._cell_text(row[eligibility_index]) == "推荐"
                for row in filtered
            ) if eligibility_index is not None else 0
            stale = sum(
                len(row) > freshness_index
                and self._cell_text(row[freshness_index]) == "过期"
                for row in filtered
            ) if freshness_index is not None else 0
            readiness = f" · 就绪 {recommended}" if eligibility_index is not None else ""
            freshness = f" · 过期 {stale}" if freshness_index is not None and stale else ""
            self.result_summary.set(
                f"当前文件：{self.current_file} · 命中 {len(filtered):,} / {len(data_rows):,} 条{readiness}{freshness}"
            )
        self.status.set(
            f"{self.current_file} · 命中 {len(filtered)} / {len(data_rows)} 条 · 第 {self._current_page + 1} / {page_count} 页 · 双击查看详情"
        )
        return True

    def load_csv(self, filename: str) -> bool:
        path = OUTPUT_DIR / filename
        self.current_file = filename
        if not path.exists():
            self._clear_result_view()
            self.status.set(f"未找到 {filename}")
            return False
        try:
            stat = path.stat()
            modified_at = (stat.st_mtime_ns, stat.st_size)
            raw_previous_token = getattr(self, "_csv_mtime", None)
            previous_token = (
                raw_previous_token
                if isinstance(raw_previous_token, tuple)
                and len(raw_previous_token) == 3
                and isinstance(raw_previous_token[0], int)
                and isinstance(raw_previous_token[1], int)
                and isinstance(raw_previous_token[2], str)
                else None
            )
            content_hash = ""
            if (
                previous_token is not None
                and self._csv_path == path
                and previous_token[:2] == modified_at
            ):
                # Some filesystems can retain an identical timestamp and size
                # for a rapid in-place CSV update.  Hash only this rare
                # collision path so the GUI never displays stale result rows.
                content_hash = hashlib.blake2b(
                    path.read_bytes(), digest_size=12
                ).hexdigest()
                needs_reload = previous_token[2] != content_hash
            else:
                needs_reload = True
            if needs_reload:
                with path.open("r", encoding="utf-8-sig", newline="") as file:
                    rows = list(csv.reader(file))
                headers = rows[0] if rows else []
                if not rows or not any(column in DISPLAY_COLUMNS for column in headers):
                    self._clear_result_view()
                    self.status.set(f"{filename} 没有可展示结果")
                    return False
                self._csv_headers = headers
                self._csv_rows = rows[1:]
                self._csv_indexes = {
                    header: index for index, header in enumerate(self._csv_headers)
                }
                self._csv_search_text = [
                    " ".join(map(self._cell_text, row)).casefold()
                    for row in self._csv_rows
                ]
                self._display_headers = [
                    column for column in DISPLAY_COLUMNS if column in self._csv_headers
                ]
                self._display_indexes = [
                    self._csv_indexes[column] for column in self._display_headers
                ]
                self._table_headers = ()
                self._csv_path = path
                if not content_hash:
                    content_hash = hashlib.blake2b(
                        path.read_bytes(), digest_size=12
                    ).hexdigest()
                self._csv_mtime = (*modified_at, content_hash)
                self._update_filter_values(self._csv_headers, self._csv_rows)
            return self._render_cached_rows()
        except (OSError, UnicodeDecodeError, csv.Error, tk.TclError) as exc:
            self._clear_result_view()
            self.status.set(f"读取 {filename} 失败")
            messagebox.showerror("读取失败", str(exc))
            return False

    def open_output(self) -> None:
        if OUTPUT_DIR.exists():
            subprocess.Popen(["explorer", str(OUTPUT_DIR)])


def main() -> None:
    root = tk.Tk()
    ScannerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
