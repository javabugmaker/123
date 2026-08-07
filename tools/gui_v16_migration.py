from pathlib import Path
import re


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"expected block not found in {path}: {old[:120]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_regex(path: str, pattern: str, replacement: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"expected one regex match in {path}, got {count}: {pattern[:120]!r}")
    p.write_text(updated, encoding="utf-8")


# ---------------------------------------------------------------------------
# gui_core.py: replace stale Quality label filtering with data-driven filters.
# ---------------------------------------------------------------------------
replace_once(
    "gui_core.py",
    '    "Quality": "质量",\n    "OverallRank": "综合排名",',
    '    "Quality": "旧质量标签",\n    "QualityGate": "基本面门槛",\n    "QualityDataAvailable": "基本面数据",\n    "ETFTheme": "ETF主题",\n    "ResearchPoolRank": "研究池排名",\n    "BacktestMode": "回测模式",\n    "BacktestCacheHit": "回测缓存",\n    "BacktestLastEvaluatedDate": "回测截至",\n    "BacktestEngine": "回测引擎",\n    "OverallRank": "综合排名",',
)

replace_once(
    "gui_core.py",
    '    "BacktestAdjustedScore",\n    "Close",',
    '    "BacktestAdjustedScore",\n    "ResearchPoolRank",\n    "Close",',
)
replace_once(
    "gui_core.py",
    '    "BacktestConfidenceTier",\n    "ChaseRiskLevel",',
    '    "BacktestConfidenceTier",\n    "BacktestMode",\n    "BacktestEngine",\n    "ETFTheme",\n    "ChaseRiskLevel",',
)
replace_once(
    "gui_core.py",
    '    "InstitutionalRank",\n    "VolAccumDays",',
    '    "InstitutionalRank",\n    "ResearchPoolRank",\n    "VolAccumDays",',
)

replace_once(
    "gui_core.py",
    '        self.quality_filter = tk.StringVar(value="全部质量")\n        self.stage_filter = tk.StringVar(value="全部阶段")',
    '        # Legacy Quality is retained for old sessions/tests, but the visible GUI\n        # now filters on actual exported fields instead of the derived label.\n        self.quality_filter = tk.StringVar(value="全部质量")\n        self.asset_filter = tk.StringVar(value="全部类型")\n        self.fundamental_filter = tk.StringVar(value="全部基本面")\n        self.tier_filter = tk.StringVar(value="全部等级")\n        self.backtest_filter = tk.StringVar(value="全部回测")\n        self.score_filter = tk.StringVar(value="全部分数")\n        self.stage_filter = tk.StringVar(value="全部阶段")',
)

replace_once(
    "gui_core.py",
    '        self.quality_filter.trace_add("write", self._schedule_filter_refresh)\n        self.stage_filter.trace_add("write", self._schedule_filter_refresh)',
    '        self.quality_filter.trace_add("write", self._schedule_filter_refresh)\n        self.asset_filter.trace_add("write", self._schedule_filter_refresh)\n        self.fundamental_filter.trace_add("write", self._schedule_filter_refresh)\n        self.tier_filter.trace_add("write", self._schedule_filter_refresh)\n        self.backtest_filter.trace_add("write", self._schedule_filter_refresh)\n        self.score_filter.trace_add("write", self._schedule_filter_refresh)\n        self.stage_filter.trace_add("write", self._schedule_filter_refresh)',
)

new_filter_ui = '''        filters = ttk.LabelFrame(self.root, text="结果筛选", padding=(12, 8))
        filters.pack(fill=tk.X, padx=18, pady=(0, 4))
        for column in range(14):
            filters.columnconfigure(column, weight=0)
        filters.columnconfigure(9, weight=1)

        ttk.Label(filters, text="类型").grid(row=0, column=0, padx=(0, 4), sticky=tk.W)
        self.asset_box = ttk.Combobox(
            filters,
            textvariable=self.asset_filter,
            values=("全部类型", "股票", "ETF"),
            state="readonly",
            width=9,
        )
        self.asset_box.grid(row=0, column=1, padx=(0, 10), sticky=tk.W)

        ttk.Label(filters, text="板块").grid(row=0, column=2, padx=(0, 4), sticky=tk.W)
        self.sector_box = ttk.Combobox(
            filters, textvariable=self.sector_filter, state="readonly", width=12
        )
        self.sector_box.grid(row=0, column=3, padx=(0, 10), sticky=tk.W)
        self.sector_box.bind("<<ComboboxSelected>>", self._sector_changed)

        ttk.Label(filters, text="行业").grid(row=0, column=4, padx=(0, 4), sticky=tk.W)
        self.industry_box = ttk.Combobox(
            filters, textvariable=self.industry_filter, state="readonly", width=14
        )
        self.industry_box.grid(row=0, column=5, padx=(0, 10), sticky=tk.W)

        ttk.Label(filters, text="资金阶段").grid(row=0, column=6, padx=(0, 4), sticky=tk.W)
        self.stage_box = ttk.Combobox(
            filters, textvariable=self.stage_filter, state="readonly", width=12
        )
        self.stage_box.grid(row=0, column=7, padx=(0, 10), sticky=tk.W)

        ttk.Label(filters, text="买点").grid(row=0, column=8, padx=(0, 4), sticky=tk.W)
        self.entry_box = ttk.Combobox(
            filters, textvariable=self.entry_filter, state="readonly", width=15
        )
        self.entry_box.grid(row=0, column=9, padx=(0, 10), sticky=tk.W)

        ttk.Label(filters, text="资格").grid(row=0, column=10, padx=(0, 4), sticky=tk.W)
        self.eligibility_box = ttk.Combobox(
            filters,
            textvariable=self.eligibility_filter,
            values=("全部资格", "推荐", "观察", "风险过滤"),
            state="readonly",
            width=9,
        )
        self.eligibility_box.grid(row=0, column=11, padx=(0, 10), sticky=tk.W)

        ttk.Label(filters, text="机构等级").grid(row=1, column=0, padx=(0, 4), pady=(8, 0), sticky=tk.W)
        self.tier_box = ttk.Combobox(
            filters, textvariable=self.tier_filter, state="readonly", width=12
        )
        self.tier_box.grid(row=1, column=1, padx=(0, 10), pady=(8, 0), sticky=tk.W)

        ttk.Label(filters, text="基本面").grid(row=1, column=2, padx=(0, 4), pady=(8, 0), sticky=tk.W)
        self.fundamental_box = ttk.Combobox(
            filters,
            textvariable=self.fundamental_filter,
            values=("全部基本面", "通过", "未通过", "数据缺失", "ETF不适用"),
            state="readonly",
            width=12,
        )
        self.fundamental_box.grid(row=1, column=3, padx=(0, 10), pady=(8, 0), sticky=tk.W)

        ttk.Label(filters, text="回测可信度").grid(row=1, column=4, padx=(0, 4), pady=(8, 0), sticky=tk.W)
        self.backtest_box = ttk.Combobox(
            filters, textvariable=self.backtest_filter, state="readonly", width=12
        )
        self.backtest_box.grid(row=1, column=5, padx=(0, 10), pady=(8, 0), sticky=tk.W)

        ttk.Label(filters, text="排序分").grid(row=1, column=6, padx=(0, 4), pady=(8, 0), sticky=tk.W)
        self.score_box = ttk.Combobox(
            filters,
            textvariable=self.score_filter,
            values=("全部分数", "≥25", "≥30", "≥35", "≥40", "≥50"),
            state="readonly",
            width=9,
        )
        self.score_box.grid(row=1, column=7, padx=(0, 10), pady=(8, 0), sticky=tk.W)

        ttk.Label(filters, text="搜索").grid(row=1, column=8, padx=(0, 4), pady=(8, 0), sticky=tk.W)
        self.search_entry = ttk.Entry(filters, textvariable=self.search, width=28)
        self.search_entry.grid(row=1, column=9, columnspan=2, padx=(0, 8), pady=(8, 0), sticky=tk.EW)
        ttk.Button(filters, text="清空筛选", command=self.clear_filters).grid(
            row=1, column=11, padx=(0, 6), pady=(8, 0), sticky=tk.W
        )
        ttk.Button(filters, text="刷新结果", command=self.refresh_results).grid(
            row=1, column=12, padx=(0, 6), pady=(8, 0), sticky=tk.W
        )

        self.market_overview = tk.StringVar(value="市场概览：等待结果")
        ttk.Label(
            filters,
            textvariable=self.market_overview,
            style="Status.TLabel",
            padding=(0, 8, 0, 0),
        ).grid(row=2, column=0, columnspan=14, sticky=tk.W)
'''
replace_regex(
    "gui_core.py",
    r'        filters = ttk\.Frame\(self\.root, style="Filter\.TFrame", padding=\(14, 8\)\).*?\n\n        body = ttk\.PanedWindow',
    new_filter_ui + '\n        body = ttk.PanedWindow',
)

replace_once(
    "gui_core.py",
    '''        self.quality_filter.set("全部质量")\n        if hasattr(self, "stage_filter"):\n            self.stage_filter.set("全部阶段")''',
    '''        self.quality_filter.set("全部质量")\n        for attribute, default in (\n            ("asset_filter", "全部类型"),\n            ("fundamental_filter", "全部基本面"),\n            ("tier_filter", "全部等级"),\n            ("backtest_filter", "全部回测"),\n            ("score_filter", "全部分数"),\n        ):\n            variable = getattr(self, attribute, None)\n            if variable is not None:\n                variable.set(default)\n        if hasattr(self, "stage_filter"):\n            self.stage_filter.set("全部阶段")''',
)

# Replace filter-value population with field-aware dynamic controls.
replace_regex(
    "gui_core.py",
    r'    def _update_filter_values\(self, headers: list\[str\], rows: list\[list\[str\]\]\) -> None:\n.*?\n    def _schedule_filter_refresh',
    '''    def _update_filter_values(self, headers: list[str], rows: list[list[str]]) -> None:\n        def values_for(column: str) -> list[str]:\n            if column not in headers:\n                return []\n            index = headers.index(column)\n            return sorted(\n                {\n                    self._cell_text(row[index])\n                    for row in rows\n                    if len(row) > index and self._cell_text(row[index])\n                }\n            )\n\n        def configure_box(widget, variable, default: str, values: list[str], enabled: bool = True) -> None:\n            options = [default, *values]\n            widget["values"] = options\n            if variable.get() not in options:\n                variable.set(default)\n            widget.configure(state="readonly" if enabled else "disabled")\n\n        sectors = values_for("Sector")\n        configure_box(self.sector_box, self.sector_filter, "全部板块", sectors, bool(sectors))\n\n        if hasattr(self, "asset_box"):\n            asset_values: set[str] = set()\n            asset_index = headers.index("AssetType") if "AssetType" in headers else -1\n            etf_index = headers.index("IsETF") if "IsETF" in headers else -1\n            for row in rows:\n                raw_asset = (\n                    self._cell_text(row[asset_index]).strip().lower()\n                    if asset_index >= 0 and len(row) > asset_index\n                    else ""\n                )\n                raw_etf = (\n                    self._cell_text(row[etf_index]).strip().lower()\n                    if etf_index >= 0 and len(row) > etf_index\n                    else ""\n                )\n                if raw_asset == "etf" or raw_etf in {"true", "1", "yes", "y", "是"}:\n                    asset_values.add("ETF")\n                elif raw_asset or raw_etf:\n                    asset_values.add("股票")\n            ordered_assets = [value for value in ("股票", "ETF") if value in asset_values]\n            configure_box(\n                self.asset_box,\n                self.asset_filter,\n                "全部类型",\n                ordered_assets,\n                asset_index >= 0 or etf_index >= 0,\n            )\n\n        if hasattr(self, "stage_box"):\n            stages = [DISPLAY_VALUE_NAMES.get(value, value) for value in values_for("SmartMoneyStage")]\n            configure_box(self.stage_box, self.stage_filter, "全部阶段", stages, bool(stages))\n        if hasattr(self, "entry_box"):\n            entries = [DISPLAY_VALUE_NAMES.get(value, value) for value in values_for("EntrySignal")]\n            configure_box(self.entry_box, self.entry_filter, "全部买点", entries, bool(entries))\n        if hasattr(self, "eligibility_box"):\n            eligibility = values_for("RankingEligibility")\n            configure_box(\n                self.eligibility_box,\n                self.eligibility_filter,\n                "全部资格",\n                eligibility,\n                "RankingEligibility" in headers,\n            )\n        if hasattr(self, "tier_box"):\n            tiers = values_for("InstitutionalTier")\n            configure_box(self.tier_box, self.tier_filter, "全部等级", tiers, bool(tiers))\n        if hasattr(self, "backtest_box"):\n            confidence = values_for("BacktestConfidenceTier")\n            configure_box(\n                self.backtest_box,\n                self.backtest_filter,\n                "全部回测",\n                confidence,\n                bool(confidence),\n            )\n        if hasattr(self, "fundamental_box"):\n            fundamental_values = ["通过", "未通过", "数据缺失"]\n            if "AssetType" in headers or "IsETF" in headers:\n                fundamental_values.append("ETF不适用")\n            fundamental_enabled = any(\n                column in headers\n                for column in ("QualityGate", "QualityDataAvailable", "QualityDataCompleteness")\n            )\n            configure_box(\n                self.fundamental_box,\n                self.fundamental_filter,\n                "全部基本面",\n                fundamental_values if fundamental_enabled else [],\n                fundamental_enabled,\n            )\n        if hasattr(self, "score_box"):\n            score_enabled = any(\n                column in headers\n                for column in ("RankingScore", "InstitutionalScore", "FinalScore", "Score")\n            )\n            configure_box(\n                self.score_box,\n                self.score_filter,\n                "全部分数",\n                ["≥25", "≥30", "≥35", "≥40", "≥50"] if score_enabled else [],\n                score_enabled,\n            )\n\n        industries = values_for("Industry")\n        if self.sector_filter.get() != "全部板块" and "Sector" in headers:\n            sector_index = headers.index("Sector")\n            industry_index = headers.index("Industry") if "Industry" in headers else -1\n            industries = sorted(\n                {\n                    self._cell_text(row[industry_index])\n                    for row in rows\n                    if industry_index >= 0\n                    and len(row) > max(sector_index, industry_index)\n                    and self._cell_text(row[sector_index]) == self.sector_filter.get()\n                    and self._cell_text(row[industry_index])\n                }\n            )\n        configure_box(\n            self.industry_box,\n            self.industry_filter,\n            "全部行业",\n            industries,\n            "Industry" in headers and bool(industries),\n        )\n\n    def _schedule_filter_refresh''',
)

# Add a single snapshot function so every view uses the same filters.
replace_once(
    "gui_core.py",
    '    def _row_matches_filters(\n        self,',
    '''    def _filter_snapshot(self) -> tuple[str, ...]:\n        def read(attribute: str, default: str) -> str:\n            variable = getattr(self, attribute, None)\n            return variable.get() if variable is not None else default\n\n        return (\n            read("sector_filter", "全部板块"),\n            read("industry_filter", "全部行业"),\n            read("quality_filter", "全部质量"),\n            read("stage_filter", "全部阶段"),\n            read("entry_filter", "全部买点"),\n            read("eligibility_filter", "全部资格"),\n            read("asset_filter", "全部类型"),\n            read("fundamental_filter", "全部基本面"),\n            read("tier_filter", "全部等级"),\n            read("backtest_filter", "全部回测"),\n            read("score_filter", "全部分数"),\n        )\n\n    def _row_matches_filters(\n        self,''',
)

replace_regex(
    "gui_core.py",
    r'    def _row_matches_filters\(\n        self,\n        indexes: dict\[str, int\],\n        row: list\[str\],\n        query: str,\n        search_text: str \| None = None,\n        filter_values: tuple\[str, str, str, str, str, str\] \| None = None,\n    \) -> bool:\n.*?\n    def _market_overview_values',
    '''    def _row_matches_filters(\n        self,\n        indexes: dict[str, int],\n        row: list[str],\n        query: str,\n        search_text: str | None = None,\n        filter_values: tuple[str, ...] | None = None,\n    ) -> bool:\n        values = (\n            row\n            if len(row) >= len(self._csv_headers)\n            else row + [""] * (len(self._csv_headers) - len(row))\n        )\n\n        def value_for(column: str) -> str:\n            index = indexes.get(column)\n            return self._cell_text(values[index]) if index is not None and index < len(values) else ""\n\n        def bool_for(column: str) -> bool | None:\n            text = value_for(column).strip().lower()\n            if not text or self._is_missing_text(text):\n                return None\n            if text in {"true", "1", "yes", "y", "是", "pass", "通过"}:\n                return True\n            if text in {"false", "0", "no", "n", "否", "fail", "未通过"}:\n                return False\n            return None\n\n        if filter_values is None:\n            filter_values = self._filter_snapshot()\n        defaults = (\n            "全部板块",\n            "全部行业",\n            "全部质量",\n            "全部阶段",\n            "全部买点",\n            "全部资格",\n            "全部类型",\n            "全部基本面",\n            "全部等级",\n            "全部回测",\n            "全部分数",\n        )\n        normalized_filters = tuple(filter_values) + defaults[len(filter_values) :]\n        (\n            sector_value,\n            industry_value,\n            quality_value,\n            stage_value,\n            entry_value,\n            eligibility_value,\n            asset_value,\n            fundamental_value,\n            tier_value,\n            backtest_value,\n            score_value,\n        ) = normalized_filters[:11]\n\n        raw_asset = value_for("AssetType").strip().lower()\n        raw_is_etf = value_for("IsETF").strip().lower()\n        has_asset_evidence = bool(raw_asset or raw_is_etf)\n        is_etf = raw_asset == "etf" or raw_is_etf in {"true", "1", "yes", "y", "是"}\n        asset_label = "ETF" if is_etf else "股票" if has_asset_evidence else ""\n\n        if is_etf:\n            fundamental_state = "ETF不适用"\n        else:\n            available = bool_for("QualityDataAvailable")\n            gate = bool_for("QualityGate")\n            completeness = self._numeric_value(value_for("QualityDataCompleteness"))\n            if available is None:\n                available = (gate is not None) or (completeness is not None and completeness > 0)\n            if not available:\n                fundamental_state = "数据缺失"\n            elif gate is True:\n                fundamental_state = "通过"\n            elif gate is False:\n                fundamental_state = "未通过"\n            else:\n                fundamental_state = "数据缺失"\n\n        ranking_value = None\n        for column in ("RankingScore", "InstitutionalScore", "FinalScore", "Score"):\n            ranking_value = self._numeric_value(value_for(column))\n            if ranking_value is not None:\n                break\n        score_match = re.search(r"([0-9]+(?:\\.[0-9]+)?)", score_value)\n        minimum_score = float(score_match.group(1)) if score_match else None\n\n        searchable = (\n            search_text\n            if search_text is not None\n            else " ".join(map(self._cell_text, values)).casefold()\n        )\n        return (\n            (not query or query in searchable)\n            and (sector_value == "全部板块" or value_for("Sector") == sector_value)\n            and (industry_value == "全部行业" or value_for("Industry") == industry_value)\n            and (quality_value == "全部质量" or value_for("Quality") == quality_value)\n            and (\n                stage_value == "全部阶段"\n                or value_for("SmartMoneyStage") == DISPLAY_VALUE_CODES.get(stage_value, stage_value)\n            )\n            and (\n                entry_value == "全部买点"\n                or value_for("EntrySignal") == DISPLAY_VALUE_CODES.get(entry_value, entry_value)\n            )\n            and (\n                eligibility_value == "全部资格"\n                or value_for("RankingEligibility") == eligibility_value\n            )\n            and (asset_value == "全部类型" or asset_label == asset_value)\n            and (fundamental_value == "全部基本面" or fundamental_state == fundamental_value)\n            and (tier_value == "全部等级" or value_for("InstitutionalTier") == tier_value)\n            and (\n                backtest_value == "全部回测"\n                or value_for("BacktestConfidenceTier") == backtest_value\n            )\n            and (\n                score_value == "全部分数"\n                or (\n                    minimum_score is not None\n                    and ranking_value is not None\n                    and ranking_value >= minimum_score\n                )\n            )\n        )\n\n    def _market_overview_values''',
)

# Use the same 11-field snapshot in the table and market overview.
replace_once(
    "gui_core.py",
    '''        filter_values = (\n            self.sector_filter.get(),\n            self.industry_filter.get(),\n            self.quality_filter.get(),\n            self.stage_filter.get() if hasattr(self, "stage_filter") else "全部阶段",\n            self.entry_filter.get() if hasattr(self, "entry_filter") else "全部买点",\n            self.eligibility_filter.get() if hasattr(self, "eligibility_filter") else "全部资格",\n        )''',
    '        filter_values = self._filter_snapshot()',
)
replace_once(
    "gui_core.py",
    '''        filter_values = (\n            self.sector_filter.get(),\n            self.industry_filter.get(),\n            self.quality_filter.get(),\n            self.stage_filter.get() if hasattr(self, "stage_filter") else "全部阶段",\n            self.entry_filter.get() if hasattr(self, "entry_filter") else "全部买点",\n            self.eligibility_filter.get() if hasattr(self, "eligibility_filter") else "全部资格",\n        )''',
    '        filter_values = self._filter_snapshot()',
)

replace_once(
    "gui_core.py",
    '        if column in {"QualityGate", "PassedFilters"}:\n            return self._format_boolean_status(text)',
    '''        if column in {"QualityGate", "PassedFilters"}:\n            return self._format_boolean_status(text)\n        if column == "QualityDataAvailable":\n            if self._is_missing_text(text):\n                return "未知"\n            return "可用" if text.lower() in {"true", "1", "yes", "y", "是"} else "缺失"\n        if column == "BacktestCacheHit":\n            if self._is_missing_text(text):\n                return "未知"\n            return "命中" if text.lower() in {"true", "1", "yes", "y", "是"} else "未命中"''',
)

replace_once(
    "gui_core.py",
    '            "QualityGate",\n            "QualityDataCompleteness",',
    '            "QualityGate",\n            "QualityDataAvailable",\n            "QualityDataCompleteness",',
)
replace_once(
    "gui_core.py",
    '            "BacktestAdjustedScore",\n            "BacktestWinRate20D",',
    '            "BacktestAdjustedScore",\n            "BacktestMode",\n            "BacktestCacheHit",\n            "BacktestLastEvaluatedDate",\n            "BacktestEngine",\n            "BacktestWinRate20D",',
)
replace_once(
    "gui_core.py",
    '            "UniverseType",\n            "SurvivorshipBiasWarning",',
    '            "UniverseType",\n            "ETFTheme",\n            "ResearchPoolRank",\n            "SurvivorshipBiasWarning",',
)

# ---------------------------------------------------------------------------
# gui.py: make sector/industry visible in the execution table.
# ---------------------------------------------------------------------------
replace_once(
    "gui.py",
    '    "Ticker",\n    "Name",\n    "Close",',
    '    "Ticker",\n    "Name",\n    "Sector",\n    "Industry",\n    "Close",',
)
replace_once(
    "gui.py",
    '        "Close": "当日收盘价",',
    '        "Close": "当日收盘价",\n        "QualityGate": "基本面门槛",\n        "QualityDataCompleteness": "基本面完整度",',
)
replace_once(
    "gui.py",
    '        "Name": 104,\n        "Close": 86,',
    '        "Name": 104,\n        "Sector": 92,\n        "Industry": 106,\n        "Close": 86,',
)

print("GUI v16 migration applied")
