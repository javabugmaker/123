from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, found {count}")
    return text.replace(old, new, 1)


def patch_analytics() -> None:
    path = Path("analytics.py")
    text = path.read_text(encoding="utf-8")

    marker = "\ndef apply_backtest_ranking(summary: BacktestSummary, top_n: int = 50) -> None:\n"
    helper = r'''

def _minimum_fast_samples_for_exact_refinement() -> int:
    """Evidence floor for promoting a FAST candidate into expensive EXACT work.

    FAST intentionally samples signals more sparsely than EXACT.  Scale the
    ranking evidence floor by the cooldown ratio so a candidate that has a
    realistic chance of reaching the normal ten-sample ranking floor is still
    eligible for refinement, while one-off signals are not recomputed exactly.
    """
    fast_cooldown = max(1, int(BACKTEST_FAST_COOLDOWN_DAYS))
    exact_cooldown = max(1, int(BACKTEST_SIGNAL_COOLDOWN_DAYS))
    return max(
        1,
        int(np.ceil(float(BACKTEST_MIN_SAMPLES_FOR_RANKING) * exact_cooldown / fast_cooldown)),
    )


def _select_exact_refinement_pool(
    frame: pd.DataFrame,
    fast_rows: list[dict[str, Any]],
    top_n: int = 50,
) -> pd.DataFrame:
    """Select only evidence-qualified, decision-relevant EXACT candidates."""
    if frame.empty:
        return frame.head(0).copy()

    working = frame.copy()
    working["_CurrentSignal"] = (
        working.get("EntrySignal", pd.Series("UNKNOWN", index=working.index))
        .fillna("UNKNOWN").astype(str).str.upper()
    )
    working["_Eligibility"] = (
        working.get("RankingEligibility", pd.Series("观察", index=working.index))
        .fillna("观察").astype(str).str.strip()
    )
    working["_RefineMetric"] = pd.to_numeric(
        working.get(
            "RankingScore",
            working.get("InstitutionalScore", working.get("FinalScore", pd.Series(np.nan, index=working.index))),
        ),
        errors="coerce",
    ).replace([np.inf, -np.inf], np.nan).fillna(-np.inf)

    by_key: dict[tuple[str, str], int] = {}
    by_ticker: dict[str, int] = {}
    for row in fast_rows:
        ticker = str(row.get("ticker", "")).strip()
        if not ticker:
            continue
        signal = str(row.get("entry_signal", "UNKNOWN")).strip().upper() or "UNKNOWN"
        try:
            samples = max(0, int(float(row.get("samples", 0) or 0)))
        except (TypeError, ValueError):
            samples = 0
        by_key[(ticker, signal)] = max(samples, by_key.get((ticker, signal), 0))
        by_ticker[ticker] = max(samples, by_ticker.get(ticker, 0))

    fast_samples: list[int] = []
    for ticker, signal in zip(
        working.get("Ticker", pd.Series("", index=working.index)).fillna("").astype(str),
        working["_CurrentSignal"],
    ):
        fast_samples.append(by_key.get((ticker, signal), by_ticker.get(ticker, 0)))
    working["_FastSamples"] = fast_samples

    ranked = (
        working.loc[~working["_Eligibility"].eq("风险过滤")]
        .sort_values("_RefineMetric", ascending=False, kind="mergesort")
        .copy()
    )
    if ranked.empty:
        return ranked
    ranked["_RefineRank"] = np.arange(1, len(ranked) + 1)
    ranked["_PriorityEligibility"] = ranked["_Eligibility"].isin({"推荐", "谨慎候选"})
    minimum_fast_samples = _minimum_fast_samples_for_exact_refinement()
    top_limit = max(1, int(top_n))
    candidate_cap = max(
        1,
        min(int(BACKTEST_EXACT_REFINEMENT_CANDIDATES), top_limit),
    )
    selected = ranked.loc[
        ranked["_FastSamples"].ge(minimum_fast_samples)
        & (ranked["_PriorityEligibility"] | ranked["_RefineRank"].le(top_limit))
    ].copy()
    return (
        selected.sort_values(
            ["_PriorityEligibility", "_RefineMetric"],
            ascending=[False, False],
            kind="mergesort",
        )
        .head(candidate_cap)
        .copy()
    )
'''
    text = replace_once(text, marker, helper + marker, "analytics helper insertion")

    old_pool = '''        rank_metric = pd.to_numeric(
            frame.get("RankingScore", frame.get("InstitutionalScore", frame.get("FinalScore"))),
            errors="coerce",
        ).fillna(-np.inf)
        eligible = ~frame.get("RankingEligibility", pd.Series("观察", index=frame.index)).fillna("观察").eq("风险过滤")
        pool = (
            frame.assign(_RefineMetric=rank_metric)
            .loc[eligible]
            .sort_values("_RefineMetric", ascending=False, kind="mergesort")
            .head(max(1, int(BACKTEST_EXACT_REFINEMENT_CANDIDATES)))
        )
        refine_tickers = pool.get("Ticker", pd.Series(dtype=str)).dropna().astype(str).tolist()
        if refine_tickers:
            logger.info("FAST screen complete; exact-refining %d candidates.", len(refine_tickers))
'''
    new_pool = '''        fast_rows = list(summary.by_ticker or [])
        pool = _select_exact_refinement_pool(frame, fast_rows, top_n=top_n)
        refine_tickers = pool.get("Ticker", pd.Series(dtype=str)).dropna().astype(str).tolist()
        if refine_tickers:
            logger.info(
                "FAST screen complete; exact-refining %d evidence-qualified candidates "
                "(min FAST samples=%d, cap=%d).",
                len(refine_tickers),
                _minimum_fast_samples_for_exact_refinement(),
                min(int(BACKTEST_EXACT_REFINEMENT_CANDIDATES), max(1, int(top_n))),
            )
'''
    text = replace_once(text, old_pool, new_pool, "analytics exact pool")
    path.write_text(text, encoding="utf-8")


def patch_gui() -> None:
    path = Path("gui.py")
    text = path.read_text(encoding="utf-8")

    old_columns = '''_core.DISPLAY_COLUMNS = (
    "OverallRank",
    "Ticker",
    "Name",
    "AssetType",
    "Industry",
    "Close",
    "EntrySignal",
    "SignalStatus",
    "SignalDays",
    "ReferenceBuyPrice",
    "StopLoss",
    "RankingEligibility",
    "RankingScore",
    "InstitutionalStrength",
    "TradeReadinessReason",
)
'''
    new_columns = '''_core.DISPLAY_COLUMNS = (
    "DisplayRank",
    "Ticker",
    "Name",
    "AssetType",
    "IndustryTopic",
    "Close",
    "EntrySignal",
    "SignalStatus",
    "SignalDays",
    "ReferenceBuyPrice",
    "StopLoss",
    "RankingEligibility",
    "RankingScore",
    "InstitutionalStrength",
    "TradeReadinessReason",
)
'''
    text = replace_once(text, old_columns, new_columns, "gui display columns")
    text = replace_once(
        text,
        '        "Close": "当日收盘价",\n',
        '        "DisplayRank": "榜单排名",\n        "IndustryTopic": "行业 / 主题",\n        "Close": "当日收盘价",\n',
        "gui column names",
    )
    text = replace_once(
        text,
        '        "OverallRank": 62,\n',
        '        "DisplayRank": 68,\n        "IndustryTopic": 112,\n        "OverallRank": 62,\n',
        "gui column widths",
    )
    text = replace_once(
        text,
        '_panel_label(top_filters, "行业", row=0, column=2, padx=(0, 4), sticky="w")',
        '_panel_label(top_filters, "行业 / 主题", row=0, column=2, padx=(0, 4), sticky="w")',
        "gui filter label",
    )
    text = replace_once(
        text,
        '("当前结果", self.card_total, "#334e68"),',
        '("资产结构", self.card_total, "#334e68"),',
        "gui asset card title",
    )

    old_filter_update = '''    _configure_filter_box(
        self.score_box,
        self.score_filter,
        "全部分数",
        ["≥25", "≥30", "≥35", "≥40", "≥50"] if score_enabled else [],
        score_enabled,
    )
'''
    new_filter_update = old_filter_update + '''
    # v28: the visible industry filter follows the same stock-industry / ETF-theme
    # projection as the table instead of showing an empty Industry for ETFs.
    topic_index = indexes.get("IndustryTopic")
    if topic_index is not None:
        topics = sorted(
            {
                self._cell_text(row[topic_index])
                for row in rows
                if len(row) > topic_index and self._cell_text(row[topic_index])
            }
        )
        self.industry_box["values"] = ["全部行业", *topics]
        if self.industry_filter.get() not in self.industry_box["values"]:
            self.industry_filter.set("全部行业")
'''
    text = replace_once(text, old_filter_update, new_filter_update, "gui topic filter values")

    start = text.index("def _row_matches_filters_v26(")
    end = text.index("\n\ndef _row_matches_filters_v16", start)
    new_filter_method = r'''def _row_matches_filters_v26(
    self,
    indexes: dict[str, int],
    row: list[str],
    query: str,
    search_text: str | None = None,
    filter_values: Sequence[str] | None = None,
) -> bool:
    if filter_values is not None:
        values = list(filter_values[:6])
        while len(values) < 6:
            values.append("")
        industry_value = values[1] or "全部行业"
        values[1] = "全部行业"
        legacy_values = tuple(values)
    else:
        industry_value = _read_filter(self, "industry_filter", "全部行业")
        legacy_values = (
            _read_filter(self, "sector_filter", "全部板块"),
            "全部行业",
            _read_filter(self, "quality_filter", "全部质量"),
            _read_filter(self, "stage_filter", "全部阶段"),
            _read_filter(self, "entry_filter", "全部买点"),
            _read_filter(self, "eligibility_filter", "全部资格"),
        )
    if not _original_row_matches_filters(self, indexes, row, query, search_text, legacy_values):
        return False

    if filter_values is not None and len(filter_values) >= 9:
        asset_value, tier_value, score_value = filter_values[6:9]
    else:
        asset_value = _read_filter(self, "asset_filter", "全部类型")
        tier_value = _read_filter(self, "tier_filter", "全部等级")
        score_value = _read_filter(self, "score_filter", "全部分数")

    padded = row if len(row) >= len(self._csv_headers) else row + [""] * (len(self._csv_headers) - len(row))
    if industry_value != "全部行业":
        topic = _value_for(indexes, padded, "IndustryTopic") or _value_for(indexes, padded, "Industry")
        if topic != industry_value:
            return False
    asset = _asset_label(indexes, padded)
    if asset_value != "全部类型" and asset != asset_value:
        return False
    if tier_value != "全部等级" and _value_for(indexes, padded, "InstitutionalTier") != tier_value:
        return False

    if score_value != "全部分数":
        threshold_match = re.search(r"([0-9]+(?:\.[0-9]+)?)", score_value)
        if threshold_match is None:
            return False
        threshold = float(threshold_match.group(1))
        ranking_value = None
        for column in ("RankingScore", "InstitutionalScore", "FinalScore", "Score"):
            ranking_value = self._numeric_value(_value_for(indexes, padded, column))
            if ranking_value is not None:
                break
        if ranking_value is None or ranking_value < threshold:
            return False

    if getattr(self, "_new_signal_only", False):
        if _value_for(indexes, padded, "SignalStatus").strip().upper() != "NEW":
            return False
    return True
'''
    text = text[:start] + new_filter_method + text[end:]

    init_old = '''        self.detail_eligibility = tk.StringVar(master=root, value="-")
        self.detail_score = tk.StringVar(master=root, value="-")
        self.detail_reason = tk.StringVar(master=root, value="双击可查看完整研究字段。")
'''
    init_new = '''        self.detail_eligibility = tk.StringVar(master=root, value="-")
        self.detail_rank = tk.StringVar(master=root, value="-")
        self.detail_score = tk.StringVar(master=root, value="-")
        self.detail_backtest = tk.StringVar(master=root, value="-")
        self.detail_reason = tk.StringVar(master=root, value="双击可查看完整研究字段。")
'''
    text = replace_once(text, init_old, init_new, "gui detail vars")

    row_old = '''        ("交易资格", self.detail_eligibility),
        ("排序 / 机构", self.detail_score),
'''
    row_new = '''        ("交易资格", self.detail_eligibility),
        ("榜单 / 全局", self.detail_rank),
        ("排序 / 机构", self.detail_score),
        ("回测证据", self.detail_backtest),
'''
    text = replace_once(text, row_old, row_new, "gui detail rows")

    start = text.index("    def _ensure_derived_columns(self) -> None:")
    end = text.index("\n    def _set_display_columns_for_file", start)
    derived = r'''    @staticmethod
    def _compact_price_range(value: str) -> str:
        text = str(value or "").strip()
        match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*[-~～—–]\s*(-?\d+(?:\.\d+)?)\s*", text)
        if not match:
            return text
        try:
            left = float(match.group(1))
            right = float(match.group(2))
        except ValueError:
            return text
        return match.group(1) if abs(left - right) <= 1e-12 else text

    @staticmethod
    def _compact_institution_tier(value: str) -> str:
        text = str(value or "").strip()
        return {
            "A级机构启动": "A",
            "B级观察": "B",
            "C级价值观察": "C",
            "D级等待确认": "D",
            "D级陷阱池": "D陷阱",
        }.get(text, text)

    def _ensure_derived_columns(self) -> None:
        if not self._csv_headers:
            return
        for column in ("DisplayRank", "IndustryTopic", "ReferenceBuyPrice", "InstitutionalStrength"):
            if column not in self._csv_headers:
                self._csv_headers.append(column)
                for row in self._csv_rows:
                    row.append("")
        indexes = {header: index for index, header in enumerate(self._csv_headers)}
        for row in self._csv_rows:
            if len(row) < len(self._csv_headers):
                row.extend([""] * (len(self._csv_headers) - len(row)))
            pool_rank = _value_for(indexes, row, "ResearchPoolRank")
            overall_rank = _value_for(indexes, row, "OverallRank")
            row[indexes["DisplayRank"]] = pool_rank or overall_rank

            asset = _asset_label(indexes, row)
            industry = _value_for(indexes, row, "Industry")
            etf_theme = _value_for(indexes, row, "ETFTheme")
            classification = _value_for(indexes, row, "ModelClassification")
            sector = _value_for(indexes, row, "Sector")
            row[indexes["IndustryTopic"]] = (
                (etf_theme or classification or sector or industry)
                if asset == "ETF"
                else (industry or classification or sector)
            )

            signal = _value_for(indexes, row, "EntrySignal").strip().upper()
            entry_zone = self._compact_price_range(_value_for(indexes, row, "EntryZone"))
            breakout = self._compact_price_range(_value_for(indexes, row, "BreakoutBuyPrice"))
            reference = breakout if signal == "BREAKOUT_CONFIRM" and breakout else entry_zone or breakout
            row[indexes["ReferenceBuyPrice"]] = reference
            tier = self._compact_institution_tier(_value_for(indexes, row, "InstitutionalTier"))
            score = _value_for(indexes, row, "InstitutionalScore")
            if tier and score:
                strength = f"{tier} · {self._format_table_value('InstitutionalScore', score)}"
            else:
                strength = tier or score
            row[indexes["InstitutionalStrength"]] = strength
        self._csv_indexes = indexes
        self._csv_search_text = [" ".join(map(self._cell_text, row)).casefold() for row in self._csv_rows]
        if hasattr(self, "industry_box"):
            _update_filter_values_v26(self, self._csv_headers, self._csv_rows)
'''
    text = text[:start] + derived + text[end:]

    start = text.index("    def _update_dashboard_cards(self) -> None:")
    end = text.index("\n    def _selected_detail", start)
    dashboard = r'''    def _update_dashboard_cards(self) -> None:
        indexes = getattr(self, "_csv_indexes", {})
        ticker_index = indexes.get("Ticker")
        if ticker_index is None:
            return
        visible = set(self.filtered_tickers)
        recommended = cautious = new_signals = stocks = etfs = 0
        for row in self._csv_rows:
            if len(row) <= ticker_index:
                continue
            ticker = self._cell_text(row[ticker_index]).upper()
            if ticker not in visible:
                continue
            eligibility = _value_for(indexes, row, "RankingEligibility")
            status = _value_for(indexes, row, "SignalStatus").strip().upper()
            asset = _asset_label(indexes, row)
            recommended += eligibility == "推荐"
            cautious += eligibility == "谨慎候选"
            new_signals += status == "NEW"
            stocks += asset == "股票"
            etfs += asset == "ETF"
        self.card_recommended.set(str(recommended))
        self.card_cautious.set(str(cautious))
        self.card_new.set(str(new_signals))
        if stocks or etfs:
            self.card_total.set(f"股票 {stocks} · ETF {etfs}")
        else:
            self.card_total.set(str(len(self.filtered_tickers)))
'''
    text = text[:start] + dashboard + text[end:]

    text = replace_once(
        text,
        '        self.detail_eligibility.set("-")\n        self.detail_score.set("-")\n        self.detail_reason.set("双击可查看完整研究字段。")\n',
        '        self.detail_eligibility.set("-")\n        self.detail_rank.set("-")\n        self.detail_score.set("-")\n        self.detail_backtest.set("-")\n        self.detail_reason.set("双击可查看完整研究字段。")\n',
        "gui detail reset",
    )

    old_detail = '''        ranking = self._format_table_value("RankingScore", data.get("RankingScore", "")) or "-"
        strength = data.get("InstitutionalStrength", "")
        if not strength:
            tier = data.get("InstitutionalTier", "")
            institution_score = self._format_table_value("InstitutionalScore", data.get("InstitutionalScore", ""))
            strength = " · ".join(value for value in (tier, institution_score) if value)
        self.detail_score.set(f"{ranking} / {strength or '-'}")
        self.detail_reason.set(data.get("TradeReadinessReason", "") or data.get("RankingReason", "") or "暂无额外执行说明。")
'''
    new_detail = '''        display_rank = self._format_table_value("ResearchPoolRank", data.get("ResearchPoolRank", ""))
        overall_rank = self._format_table_value("OverallRank", data.get("OverallRank", ""))
        self.detail_rank.set(f"{display_rank or '-'} / {overall_rank or '-'}")
        ranking = self._format_table_value("RankingScore", data.get("RankingScore", "")) or "-"
        strength = data.get("InstitutionalStrength", "")
        if not strength:
            tier = self._compact_institution_tier(data.get("InstitutionalTier", ""))
            institution_score = self._format_table_value("InstitutionalScore", data.get("InstitutionalScore", ""))
            strength = " · ".join(value for value in (tier, institution_score) if value)
        self.detail_score.set(f"{ranking} / {strength or '-'}")
        mode = str(data.get("BacktestMode", "") or "").strip().upper()
        samples_value = self._numeric_value(data.get("BacktestSamples", ""))
        samples = int(samples_value) if samples_value is not None else 0
        confidence = str(data.get("BacktestConfidenceTier", "") or "").strip() or "未评估"
        backtest_parts = [value for value in (mode, f"{samples}样本", confidence) if value]
        self.detail_backtest.set(" · ".join(backtest_parts) or "-")
        reason = data.get("TradeReadinessReason", "") or data.get("RankingReason", "") or "暂无额外执行说明。"
        if confidence == "样本不足":
            reason = f"{reason}\n\n历史样本不足，回测暂不作为主要排序依据。"
        self.detail_reason.set(reason)
'''
    text = replace_once(text, old_detail, new_detail, "gui detail backtest")

    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_analytics()
    patch_gui()
    print("v28 GUI + exact refinement patch applied")


if __name__ == "__main__":
    main()
