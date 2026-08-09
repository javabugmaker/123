from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

scanner_path = ROOT / "scanner.py"
text = scanner_path.read_text(encoding="utf-8")
old = '''        market_cap = ticker_info.market_cap\n        if market_cap is None and not ticker_info.is_etf:\n            try:\n                market_cap = get_market_cap(ticker)\n            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:\n                return ScanResult(\n                    ticker=ticker,\n                    name=ticker_info.name,\n                    sector=ticker_info.sector,\n                    industry=ticker_info.industry,\n                    is_etf=ticker_info.is_etf,\n                    asset_type=ticker_info.asset_type,\n                    error=f"市值获取失败: {exc}",\n                )\n        if (\n            not ticker_info.is_etf\n            and market_cap is not None\n            and market_cap < MIN_MARKET_CAP\n        ):\n            return ScanResult(\n                ticker=ticker,\n                name=ticker_info.name,\n                sector=ticker_info.sector,\n                industry=ticker_info.industry,\n                is_etf=ticker_info.is_etf,\n                asset_type=ticker_info.asset_type,\n                error=f"市值 {market_cap:,.0f} 元低于最低要求 {MIN_MARKET_CAP:,.0f} 元",\n            )\n\n'''
if old in text:
    scanner_path.write_text(text.replace(old, "", 1), encoding="utf-8")
elif "市值获取失败:" in text:
    raise RuntimeError("legacy fatal market-cap branch still present but pattern changed")

# v30's regression contract should protect the minimum engineering generation,
# not freeze PIPELINE_VERSION forever at exactly v30.
test_path = ROOT / "test_v30_performance_workstation.py"
test_text = test_path.read_text(encoding="utf-8")
old_test = '        self.assertIn("v30", config.PIPELINE_VERSION)\n'
new_test = '        self.assertTrue(any(f"v{version}" in config.PIPELINE_VERSION for version in range(30, 100)))\n'
if old_test in test_text:
    test_path.write_text(test_text.replace(old_test, new_test, 1), encoding="utf-8")
elif new_test not in test_text:
    raise RuntimeError("v30 engineering-version assertion not found")

print("v31 compatibility fix applied")
