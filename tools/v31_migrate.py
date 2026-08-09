from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"pattern not found in {path}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# ---------------------------------------------------------------------------
# downloader.py: normalize ambiguous TickFlow CN share-capital metadata.
# ---------------------------------------------------------------------------
path = ROOT / "downloader.py"
replace_once(
    path,
    '''def _number_or_none(value: Any) -> float | None:\n    try:\n        number = float(value)\n    except (TypeError, ValueError):\n        return None\n    return number if np.isfinite(number) and number > 0 else None\n\n\ndef _as_mapping(value: Any) -> dict[str, Any]:\n''',
    '''def _number_or_none(value: Any) -> float | None:\n    try:\n        number = float(value)\n    except (TypeError, ValueError):\n        return None\n    return number if np.isfinite(number) and number > 0 else None\n\n\ndef _normalize_cn_share_count(value: Any) -> float | None:\n    \"\"\"Return TickFlow CN share-capital metadata in individual shares.\n\n    Historical/free metadata payloads can expose CN share capital at a scale\n    that is indistinguishable from 10k-share units.  Treating those small\n    values as individual shares makes almost the entire A-share universe look\n    smaller than the 100m CNY market-cap floor.  Values already large enough\n    to be plausible individual-share counts are preserved; smaller positive\n    values are conservatively expanded by 10,000.\n    \"\"\"\n    number = _number_or_none(value)\n    if number is None:\n        return None\n    if number < 10_000_000:\n        scaled = number * 10_000.0\n        if scaled <= 10_000_000_000_000.0:\n            return scaled\n    return number\n\n\ndef _as_mapping(value: Any) -> dict[str, Any]:\n''',
)
replace_once(
    path,
    '''    total_shares = _number_or_none(ext.get("total_shares"))\n    float_shares = _number_or_none(ext.get("float_shares"))\n''',
    '''    total_shares = _normalize_cn_share_count(ext.get("total_shares"))\n    float_shares = _normalize_cn_share_count(ext.get("float_shares"))\n''',
)
replace_once(
    path,
    '''    shares = _number_or_none(ext.get("total_shares"))\n    if shares is None:\n        return None\n''',
    '''    shares = _normalize_cn_share_count(ext.get("total_shares"))\n    if shares is None:\n        return None\n''',
)


# ---------------------------------------------------------------------------
# scanner.py: market-cap failures are filter evidence, never fatal scan errors.
# Prefer universe metadata already loaded by TickFlow instead of refetching.
# ---------------------------------------------------------------------------
path = ROOT / "scanner.py"
replace_once(
    path,
    '''        market_cap = ticker_info.market_cap\n        if market_cap is None and not ticker_info.is_etf:\n            try:\n                market_cap = get_market_cap(ticker)\n            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:\n                return ScanResult(\n                    ticker=ticker,\n                    name=ticker_info.name,\n                    sector=ticker_info.sector,\n                    industry=ticker_info.industry,\n                    is_etf=ticker_info.is_etf,\n                    asset_type=ticker_info.asset_type,\n                    error=f"市值获取失败: {exc}",\n                )\n        if (\n            not ticker_info.is_etf\n            and market_cap is not None\n            and market_cap < MIN_MARKET_CAP\n        ):\n            return ScanResult(\n                ticker=ticker,\n                name=ticker_info.name,\n                sector=ticker_info.sector,\n                industry=ticker_info.industry,\n                is_etf=ticker_info.is_etf,\n                asset_type=ticker_info.asset_type,\n                error=f"市值 {market_cap:,.0f} 元低于最低要求 {MIN_MARKET_CAP:,.0f} 元",\n            )\n\n        if not indicators_computed:\n            df = compute_all_indicators(df.copy())\n        close = _parse_float(df["Close"].iloc[-1], np.nan)\n        if not np.isfinite(close):\n''',
    '''        if not indicators_computed:\n            df = compute_all_indicators(df.copy())\n        close = _parse_float(df["Close"].iloc[-1], np.nan)\n        if not np.isfinite(close):\n''',
)
replace_once(
    path,
    '''        filter_results = run_all_filters(\n            df,\n            market_cap=market_cap,\n            require_market_cap=not ticker_info.is_etf,\n        )\n''',
    '''        market_cap = ticker_info.market_cap\n        if market_cap is None and not ticker_info.is_etf:\n            shares = _parse_float(ticker_info.total_shares, np.nan)\n            if np.isfinite(shares) and shares > 0:\n                market_cap = float(shares * close)\n            else:\n                try:\n                    market_cap = get_market_cap(ticker)\n                except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:\n                    logger.warning(\n                        "Market-cap metadata unavailable for %s; treating as unknown: %s",\n                        ticker,\n                        exc,\n                    )\n                    market_cap = None\n\n        # Market-cap evidence participates in the ordinary filter gate.  A low\n        # or missing value must never become ScanResult.error, because errors\n        # are excluded from every candidate export and can silently collapse\n        # the entire stock research pool when provider metadata changes scale.\n        filter_results = run_all_filters(\n            df,\n            market_cap=market_cap,\n            require_market_cap=not ticker_info.is_etf,\n        )\n''',
)
replace_once(
    path,
    '''            "min_market_cap": filter_results.min_market_cap.passed,\n            "sufficient_history": filter_results.sufficient_history.passed,\n''',
    '''            "min_market_cap": filter_results.min_market_cap.passed,\n            "market_cap": (\n                float(market_cap)\n                if market_cap is not None and np.isfinite(float(market_cap))\n                else None\n            ),\n            "market_cap_available": bool(\n                market_cap is not None and np.isfinite(float(market_cap))\n            ),\n            "sufficient_history": filter_results.sufficient_history.passed,\n''',
)


# ---------------------------------------------------------------------------
# report.py: persist market-cap provenance for future diagnostics.
# ---------------------------------------------------------------------------
path = ROOT / "report.py"
replace_once(
    path,
    '''                "Close": r.close,\n                "Score": round(r.score.total, 2),\n''',
    '''                "Close": r.close,\n                "MarketCap": r.filter_details.get("market_cap"),\n                "MarketCapDataAvailable": bool(\n                    r.filter_details.get("market_cap_available", False)\n                ),\n                "MarketCapPassed": bool(\n                    r.filter_details.get("min_market_cap", False)\n                ),\n                "Score": round(r.score.total, 2),\n''',
)


# ---------------------------------------------------------------------------
# daily_pipeline.py: distinguish raw universe rows from valid non-error rows,
# reject a collapsed stock/ETF result surface, and require a complete split Top50.
# ---------------------------------------------------------------------------
path = ROOT / "daily_pipeline.py"
replace_once(
    path,
    '''    DAILY_MIN_FRESH_RATIO,\n    DAILY_MIN_STOCK_COUNT,\n    DAILY_MIN_UNIVERSE_TOTAL,\n''',
    '''    DAILY_MIN_FRESH_RATIO,\n    DAILY_MIN_STOCK_COUNT,\n    DAILY_MIN_UNIVERSE_TOTAL,\n    DAILY_MIN_VALID_ETF_RATIO,\n    DAILY_MIN_VALID_STOCK_RATIO,\n''',
)
replace_once(
    path,
    '''        "stocks": 0,\n        "etfs": 0,\n        "fresh_rows": 0,\n''',
    '''        "stocks": 0,\n        "etfs": 0,\n        "valid_rows": 0,\n        "valid_stocks": 0,\n        "valid_etfs": 0,\n        "error_rows": 0,\n        "valid_stock_ratio": 0.0,\n        "valid_etf_ratio": 0.0,\n        "fresh_rows": 0,\n''',
)
replace_once(
    path,
    '''    run_ids: set[str] = set()\n    rows = stocks = etfs = fresh = 0\n''',
    '''    run_ids: set[str] = set()\n    rows = stocks = etfs = fresh = 0\n    valid_rows = valid_stocks = valid_etfs = error_rows = 0\n''',
)
replace_once(
    path,
    '''                if asset == "etf" or _truthy(row.get("IsETF", False)):\n                    etfs += 1\n                else:\n                    stocks += 1\n                if str(row.get("DataAsOf", "")).strip() == expected_date:\n''',
    '''                row_is_etf = asset == "etf" or _truthy(row.get("IsETF", False))\n                if row_is_etf:\n                    etfs += 1\n                else:\n                    stocks += 1\n                has_error = bool(str(row.get("Error", "")).strip())\n                if has_error:\n                    error_rows += 1\n                else:\n                    valid_rows += 1\n                    if row_is_etf:\n                        valid_etfs += 1\n                    else:\n                        valid_stocks += 1\n                if str(row.get("DataAsOf", "")).strip() == expected_date:\n''',
)
replace_once(
    path,
    '''            "stocks": stocks,\n            "etfs": etfs,\n            "fresh_rows": fresh,\n            "fresh_ratio": round(fresh / rows, 4) if rows else 0.0,\n''',
    '''            "stocks": stocks,\n            "etfs": etfs,\n            "valid_rows": valid_rows,\n            "valid_stocks": valid_stocks,\n            "valid_etfs": valid_etfs,\n            "error_rows": error_rows,\n            "valid_stock_ratio": round(valid_stocks / stocks, 4) if stocks else 0.0,\n            "valid_etf_ratio": round(valid_etfs / etfs, 4) if etfs else 0.0,\n            "fresh_rows": fresh,\n            "fresh_ratio": round(fresh / rows, 4) if rows else 0.0,\n''',
)
replace_once(
    path,
    '''    stocks = int(scan_profile.get("stocks", 0) or 0)\n    etfs = int(scan_profile.get("etfs", 0) or 0)\n    fresh_ratio = float(scan_profile.get("fresh_ratio", 0.0) or 0.0)\n''',
    '''    stocks = int(scan_profile.get("stocks", 0) or 0)\n    etfs = int(scan_profile.get("etfs", 0) or 0)\n    valid_stocks = int(scan_profile.get("valid_stocks", stocks) or 0)\n    valid_etfs = int(scan_profile.get("valid_etfs", etfs) or 0)\n    valid_stock_ratio = float(\n        scan_profile.get("valid_stock_ratio", valid_stocks / max(1, stocks)) or 0.0\n    )\n    valid_etf_ratio = float(\n        scan_profile.get("valid_etf_ratio", valid_etfs / max(1, etfs)) or 0.0\n    )\n    fresh_ratio = float(scan_profile.get("fresh_ratio", 0.0) or 0.0)\n''',
)
replace_once(
    path,
    '''    if etfs < int(DAILY_MIN_ETF_COUNT):\n        errors.append(f"ETF仅 {etfs}，低于安全下限 {DAILY_MIN_ETF_COUNT}")\n    if fresh_ratio < float(DAILY_MIN_FRESH_RATIO):\n''',
    '''    if etfs < int(DAILY_MIN_ETF_COUNT):\n        errors.append(f"ETF仅 {etfs}，低于安全下限 {DAILY_MIN_ETF_COUNT}")\n    if valid_stocks < int(DAILY_MIN_STOCK_COUNT):\n        errors.append(\n            f"有效股票仅 {valid_stocks}/{stocks}，低于安全下限 {DAILY_MIN_STOCK_COUNT}"\n        )\n    if stocks and valid_stock_ratio < float(DAILY_MIN_VALID_STOCK_RATIO):\n        errors.append(\n            f"股票有效率 {valid_stock_ratio:.1%}，低于 {DAILY_MIN_VALID_STOCK_RATIO:.0%}"\n        )\n    if valid_etfs < int(DAILY_MIN_ETF_COUNT):\n        errors.append(\n            f"有效ETF仅 {valid_etfs}/{etfs}，低于安全下限 {DAILY_MIN_ETF_COUNT}"\n        )\n    if etfs and valid_etf_ratio < float(DAILY_MIN_VALID_ETF_RATIO):\n        errors.append(\n            f"ETF有效率 {valid_etf_ratio:.1%}，低于 {DAILY_MIN_VALID_ETF_RATIO:.0%}"\n        )\n    if fresh_ratio < float(DAILY_MIN_FRESH_RATIO):\n''',
)
replace_once(
    path,
    '''    for name in FINAL_OUTPUTS:\n        profile = profiles.get(name, {})\n        if int(profile.get("rows", 0) or 0) <= 0:\n            errors.append(f"{name} 缺失或为空")\n            continue\n''',
    '''    expected_split_rows = {\n        "Top50Mixed.csv": min(\n            int(TOP_N_REPORT), int(scan_profile.get("valid_rows", 0) or 0)\n        ),\n        "Top50Stocks.csv": min(\n            int(TOP_N_REPORT), int(scan_profile.get("valid_stocks", 0) or 0)\n        ),\n        "Top50ETF.csv": min(\n            int(TOP_N_REPORT), int(scan_profile.get("valid_etfs", 0) or 0)\n        ),\n    }\n    for name in FINAL_OUTPUTS:\n        profile = profiles.get(name, {})\n        rows = int(profile.get("rows", 0) or 0)\n        if rows <= 0:\n            errors.append(f"{name} 缺失或为空")\n            continue\n        expected_rows = expected_split_rows.get(name, 0)\n        if quality_gates and expected_rows and rows < expected_rows:\n            errors.append(\n                f"{name} 仅 {rows} 条，当前有效标的足以生成 {expected_rows} 条"\n            )\n''',
)


# ---------------------------------------------------------------------------
# config.py: v31 engineering version and validity-ratio gates.
# ---------------------------------------------------------------------------
path = ROOT / "config.py"
replace_once(
    path,
    '''PIPELINE_VERSION: str = "2026-08-09-v30-fast-workstation"\nGUI_VERSION: str = "2026-08-09-v30-workstation"\nBACKTEST_PROVENANCE_VERSION: str = "2026-08-09-v30"\n''',
    '''PIPELINE_VERSION: str = "2026-08-10-v31-stock-universe-integrity"\nGUI_VERSION: str = "2026-08-09-v30-workstation"\nBACKTEST_PROVENANCE_VERSION: str = "2026-08-09-v30"\n''',
)
replace_once(
    path,
    '''DAILY_MIN_FRESH_RATIO: Final[float] = 0.90\nDAILY_RELATIVE_UNIVERSE_FLOOR: Final[float] = 0.60\n''',
    '''DAILY_MIN_FRESH_RATIO: Final[float] = 0.90\nDAILY_RELATIVE_UNIVERSE_FLOOR: Final[float] = 0.60\nDAILY_MIN_VALID_STOCK_RATIO: Final[float] = 0.60\nDAILY_MIN_VALID_ETF_RATIO: Final[float] = 0.60\n''',
)


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------
(ROOT / "test_v31_stock_universe_integrity.py").write_text(
    r'''from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import daily_pipeline
import downloader
import scanner
from downloader import TickerInfo
from filters import AllFilterResults, FilterResult
from score import ScoreBreakdown


class V31StockUniverseIntegrityTests(unittest.TestCase):
    def test_small_tickflow_share_metadata_is_normalized_from_10k_scale(self):
        self.assertEqual(downloader._normalize_cn_share_count(500_000), 5_000_000_000)
        self.assertEqual(downloader._normalize_cn_share_count(50_000_000), 50_000_000)
        self.assertIsNone(downloader._normalize_cn_share_count(None))

    def test_ticker_info_from_meta_normalizes_total_and_float_shares(self):
        info = downloader._ticker_info_from_meta(
            "000001.SZ",
            {"name": "测试银行", "ext": {"total_shares": 500_000, "float_shares": 450_000}},
            False,
        )
        self.assertEqual(info.total_shares, 5_000_000_000)
        self.assertEqual(info.float_shares, 4_500_000_000)

    def test_market_cap_provider_failure_is_not_a_scan_error(self):
        index = pd.date_range("2024-01-01", periods=260, freq="B")
        frame = pd.DataFrame(
            {
                "Open": 10.0,
                "High": 10.5,
                "Low": 9.5,
                "Close": 10.0,
                "Volume": 1_000_000.0,
                "MA20": 10.0,
                "MA50": 10.0,
                "ATR14": 0.5,
                "ATR50": 0.6,
                "RSI14": 50.0,
                "OBV": np.arange(260, dtype=float),
                "CMF": 0.1,
                "AD": np.arange(260, dtype=float),
                "DistToLow52W": 10.0,
                "WyckoffPhase": "ACCUMULATION",
            },
            index=index,
        )
        filters = AllFilterResults(
            min_price=FilterResult(True),
            min_volume=FilterResult(True),
            min_market_cap=FilterResult(False, "市值数据不可用"),
            sufficient_history=FilterResult(True),
            bear_market=FilterResult(False),
            consolidation=FilterResult(True),
            volume_accumulation=FilterResult(True, details={"consecutive_days": 20}),
            obv_divergence=FilterResult(True),
            cmf_positive=FilterResult(True, details={"cmf_improving": True}),
            ad_slope=FilterResult(True),
            volatility_contraction=FilterResult(True),
        )
        score = ScoreBreakdown(total=50.0, trend=10.0, volume=10.0, accumulation=10.0, volatility=10.0, structure=10.0)
        with patch.object(scanner, "get_market_cap", side_effect=OSError("metadata unavailable")), \
             patch.object(scanner, "run_all_filters", return_value=filters), \
             patch.object(scanner, "score_ticker", return_value=score), \
             patch.object(scanner, "classify_style", return_value="均衡"), \
             patch.object(scanner, "get_quality") as quality, \
             patch.object(scanner, "entry_point", return_value={
                 "low": 9.5, "high": 10.0, "score": 50.0, "signal": "WAIT_PULLBACK",
                 "breakout": 10.5, "stop": 9.0, "zone_distance_pct": 0.0,
                 "zone_distance_atr": 0.0, "pullback_quality": 50.0,
                 "volume_ratio": 1.0, "volume_confirmed": False,
                 "flow_confirmed": False, "price_breakout": False,
             }), \
             patch.object(scanner, "smart_money_stage", return_value="ACCUMULATION"):
            quality.return_value = type("Q", (), {
                "industry": "", "roe": np.nan, "gross_margin": np.nan,
                "institution_holding_trend": None, "institution_holding_periods": np.nan,
                "net_profit_y1": np.nan, "net_profit_y2": np.nan, "net_profit_y3": np.nan,
                "industry_gross_margin_percentile": np.nan, "roe_factor": False,
                "gross_margin_factor": False, "institution_holding_factor": False,
                "net_profit_factor": False, "quality_score": np.nan, "quality_gate": True,
                "quality_reason": "missing", "data_available": False, "applicable": True,
                "institution_holding_status": "UNKNOWN", "quality_data_completeness": 0.0,
                "quality_gate_reason": "missing", "quality_multiplier": 0.95,
            })()
            result = scanner.scan_single_from_df(
                TickerInfo(ticker="000001.SZ", name="测试", asset_type="stock"),
                frame,
                indicators_computed=True,
            )
        self.assertEqual(result.error, "")
        self.assertFalse(result.filter_details["market_cap_available"])
        self.assertFalse(result.filter_details["min_market_cap"])

    def test_csv_profile_counts_valid_stock_rows_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "AllResults.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=["Ticker", "AssetType", "IsETF", "DataAsOf", "RunId", "Error"])
                writer.writeheader()
                writer.writerow({"Ticker": "000001.SZ", "AssetType": "stock", "IsETF": False, "DataAsOf": "2026-08-07", "RunId": "r", "Error": ""})
                writer.writerow({"Ticker": "000002.SZ", "AssetType": "stock", "IsETF": False, "DataAsOf": "2026-08-07", "RunId": "r", "Error": "市值错误"})
                writer.writerow({"Ticker": "510300.SH", "AssetType": "etf", "IsETF": True, "DataAsOf": "2026-08-07", "RunId": "r", "Error": ""})
            profile = daily_pipeline._csv_profile(path, "2026-08-07")
        self.assertEqual(profile["stocks"], 2)
        self.assertEqual(profile["valid_stocks"], 1)
        self.assertEqual(profile["valid_etfs"], 1)
        self.assertEqual(profile["error_rows"], 1)
        self.assertEqual(profile["valid_stock_ratio"], 0.5)

    def test_quality_gate_rejects_stock_validity_collapse(self):
        profile = {
            "rows": 6800, "stocks": 5300, "etfs": 1500,
            "valid_rows": 1501, "valid_stocks": 1, "valid_etfs": 1500,
            "valid_stock_ratio": 1 / 5300, "valid_etf_ratio": 1.0,
            "fresh_ratio": 1.0,
        }
        errors = daily_pipeline._quality_gate_errors(profile, {}, quality_gates=True)
        self.assertTrue(any("有效股票仅 1/5300" in item for item in errors))
        self.assertTrue(any("股票有效率" in item for item in errors))

    def test_final_gate_requires_full_stock_top50_when_pool_is_large(self):
        scan_profile = {
            "run_ids": ["r"], "valid_rows": 6800, "valid_stocks": 5300,
            "valid_etfs": 1500,
        }
        profiles = {
            "Top50Mixed.csv": {"rows": 50, "fresh_ratio": 1.0, "run_ids": ["r"]},
            "Top50Stocks.csv": {"rows": 1, "fresh_ratio": 1.0, "run_ids": ["r"]},
            "Top50ETF.csv": {"rows": 50, "fresh_ratio": 1.0, "run_ids": ["r"]},
        }
        errors = daily_pipeline._final_output_errors(scan_profile, profiles, quality_gates=True)
        self.assertTrue(any("Top50Stocks.csv 仅 1 条" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
''',
    encoding="utf-8",
)

print("v31 migration applied")
