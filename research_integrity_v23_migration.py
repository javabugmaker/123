from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    if old not in text:
        raise RuntimeError(f"anchor not found in {path}: {old[:120]!r}")
    if text.count(old) != 1:
        raise RuntimeError(f"anchor not unique in {path}: {old[:120]!r}")
    write(path, text.replace(old, new, 1))


def insert_after_once(path: str, anchor: str, addition: str) -> None:
    text = read(path)
    if addition.strip() in text:
        return
    if text.count(anchor) != 1:
        raise RuntimeError(f"insert anchor not unique in {path}: {anchor[:120]!r}")
    write(path, text.replace(anchor, anchor + addition, 1))


def replace_before(path: str, start: str, end: str, replacement: str) -> None:
    text = read(path)
    begin = text.find(start)
    finish = text.find(end, begin + len(start)) if begin >= 0 else -1
    if begin < 0 or finish < 0:
        raise RuntimeError(f"region anchors not found in {path}: {start!r} -> {end!r}")
    write(path, text[:begin] + replacement + text[finish:])


TRADING_CALENDAR = '''from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

try:
    import holidays
except ImportError:  # pragma: no cover - requirements install provides it
    holidays = None

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_CLOSE_MINUTE = 15 * 60


@lru_cache(maxsize=16)
def _china_holidays(year: int) -> frozenset[date]:
    if holidays is None:
        return frozenset()
    try:
        calendar = holidays.country_holidays("CN", years=[int(year)])
    except Exception:
        return frozenset()
    return frozenset(day for day in calendar.keys() if isinstance(day, date))


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in _china_holidays(day.year)


def latest_completed_trading_day(now: datetime | None = None) -> date:
    current = now or datetime.now(_SHANGHAI)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_SHANGHAI)
    else:
        current = current.astimezone(_SHANGHAI)
    candidate = current.date()
    minute = current.hour * 60 + current.minute
    if is_trading_day(candidate) and minute >= _CLOSE_MINUTE:
        return candidate
    candidate -= timedelta(days=1)
    while not is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def trading_age_days(asof: date, now: datetime | None = None) -> int:
    target = latest_completed_trading_day(now)
    if asof >= target:
        return 0
    count = 0
    cursor = asof + timedelta(days=1)
    while cursor <= target:
        if is_trading_day(cursor):
            count += 1
        cursor += timedelta(days=1)
    return count


def market_is_closed(now: datetime | None = None) -> bool:
    current = now or datetime.now(_SHANGHAI)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_SHANGHAI)
    else:
        current = current.astimezone(_SHANGHAI)
    return (
        not is_trading_day(current.date())
        or current.hour * 60 + current.minute >= _CLOSE_MINUTE
    )
'''

TRADEABILITY = '''from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

_LIMIT_TOLERANCE = 0.0025


def daily_limit_pct(ticker: str, *, is_etf: bool = False) -> float:
    symbol = str(ticker or "").strip().upper()
    code = symbol.split(".", 1)[0]
    suffix = symbol.rsplit(".", 1)[-1] if "." in symbol else ""
    if suffix == "BJ":
        return 0.30
    if not is_etf and code.startswith(("300", "301", "688", "689")):
        return 0.20
    return 0.10


def _number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return np.nan
    return result if np.isfinite(result) else np.nan


def is_entry_tradeable(
    ticker: str,
    frame: pd.DataFrame,
    entry_index: int,
    *,
    is_etf: bool = False,
) -> tuple[bool, str]:
    required = {"Open", "High", "Low", "Close", "Volume"}
    if frame is None or entry_index <= 0 or entry_index >= len(frame):
        return False, "invalid_entry_index"
    if not required.issubset(frame.columns):
        return False, "missing_ohlcv"

    row = frame.iloc[int(entry_index)]
    previous = frame.iloc[int(entry_index) - 1]
    open_price = _number(row["Open"])
    high = _number(row["High"])
    low = _number(row["Low"])
    volume = _number(row["Volume"])
    previous_close = _number(previous["Close"])
    if not all(np.isfinite(value) and value > 0 for value in (open_price, high, low, previous_close)):
        return False, "invalid_price"
    if not np.isfinite(volume) or volume <= 0:
        return False, "suspended_or_zero_volume"

    limit_pct = daily_limit_pct(ticker, is_etf=is_etf)
    theoretical_limit_up = previous_close * (1.0 + limit_pct)
    threshold = theoretical_limit_up * (1.0 - _LIMIT_TOLERANCE)
    if open_price >= threshold and low >= threshold:
        return False, "locked_limit_up"
    return True, "tradeable"
'''

CALIBRATION_BRIDGE = '''from __future__ import annotations

from typing import Any

import numpy as np


def _number(value: Any, default: float = np.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if np.isfinite(result) else default


def _key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("ticker", "")),
        str(row.get("entry_signal", "UNKNOWN")).upper(),
    )


def _score(row: dict[str, Any]) -> float:
    adjusted = _number(row.get("backtest_adjusted_score"))
    if np.isfinite(adjusted):
        return adjusted
    return _number(row.get("backtest_score"))


def bridge_global_calibration(
    global_rows: list[dict[str, Any]] | None,
    fast_rows: list[dict[str, Any]] | None,
    exact_rows: list[dict[str, Any]] | None,
    *,
    min_samples: int = 10,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = [dict(row) for row in (global_rows or [])]
    fast_map = {_key(row): row for row in (fast_rows or [])}
    differences: list[float] = []
    for exact in exact_rows or []:
        key = _key(exact)
        fast = fast_map.get(key)
        if fast is None:
            continue
        if int(_number(exact.get("samples"), 0.0)) < int(min_samples):
            continue
        if int(_number(fast.get("samples"), 0.0)) < int(min_samples):
            continue
        exact_score = _score(exact)
        fast_score = _score(fast)
        if np.isfinite(exact_score) and np.isfinite(fast_score):
            differences.append(float(exact_score - fast_score))

    metadata: dict[str, Any] = {
        "accepted": False,
        "pairs": len(differences),
        "median_score_delta": 0.0,
        "mad": 0.0,
        "confidence": 0.0,
        "applied_delta": 0.0,
    }
    if len(differences) < 5 or not rows:
        return rows, metadata

    values = np.asarray(differences, dtype=float)
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    pair_confidence = float(np.clip(len(values) / 30.0, 0.0, 1.0))
    stability = float(np.clip(1.0 - mad / 20.0, 0.25, 1.0))
    confidence = pair_confidence * stability
    applied_delta = float(np.clip(median, -8.0, 8.0) * confidence)

    adjusted: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        score = _number(item.get("calibration_score"), 50.0)
        row_confidence = float(np.clip(_number(item.get("confidence"), 0.0), 0.0, 1.0))
        item["calibration_score"] = round(
            float(np.clip(score + applied_delta * row_confidence, 0.0, 100.0)), 4
        )
        item["fast_exact_bridge_delta"] = round(applied_delta, 4)
        adjusted.append(item)

    metadata.update(
        {
            "accepted": True,
            "median_score_delta": round(median, 4),
            "mad": round(mad, 4),
            "confidence": round(confidence, 4),
            "applied_delta": round(applied_delta, 4),
        }
    )
    return adjusted, metadata
'''

HISTORICAL_UNIVERSE = '''from __future__ import annotations

import re
from pathlib import Path

from config import CACHE_DIR
from downloader import normalize_ticker

_CACHE_DIR = CACHE_DIR / "v3-tickflow-forward"
_BENCHMARKS = {"000300.SH", "000905.SH", "399006.SZ"}
_SECURITY = re.compile(r"^\\d{6}\\.(?:SH|SZ|BJ)$")


def cached_security_tickers(cache_dir: Path | None = None) -> list[str]:
    directory = cache_dir or _CACHE_DIR
    if not directory.exists():
        return []
    result: set[str] = set()
    for path in directory.glob("*.parquet"):
        ticker = normalize_ticker(path.stem)
        if ticker in _BENCHMARKS or not _SECURITY.fullmatch(ticker):
            continue
        result.add(ticker)
    return sorted(result)


def merge_with_cached_universe(
    current_tickers: list[str],
    cache_dir: Path | None = None,
) -> list[str]:
    combined = [normalize_ticker(ticker) for ticker in current_tickers if str(ticker).strip()]
    combined.extend(cached_security_tickers(cache_dir))
    return list(dict.fromkeys(combined))
'''

TESTS = '''from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import score
from calibration_bridge import bridge_global_calibration
from config import MODEL_QUALITY_WEIGHT
from historical_universe import merge_with_cached_universe
from tradeability import daily_limit_pct, is_entry_tradeable
from trading_calendar import is_trading_day, trading_age_days


class ResearchIntegrityV23Tests(unittest.TestCase):
    def test_china_holiday_is_not_trading_day(self):
        self.assertFalse(is_trading_day(date(2026, 10, 1)))
        now = datetime(2026, 10, 1, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(trading_age_days(date(2026, 9, 30), now), 0)

    def test_calibration_weights_hot_reload(self):
        original_output = score.OUTPUT_DIR
        with tempfile.TemporaryDirectory() as tmp:
            score.OUTPUT_DIR = Path(tmp)
            score.invalidate_model_weight_cache()
            path = score.OUTPUT_DIR / "ScoreCalibration.json"
            path.write_text(json.dumps({
                "accepted": True,
                "setup_weight": 0.60,
                "trigger_weight": 0.25,
                "execution_weight": 0.15,
            }), encoding="utf-8")
            first = score._model_component_weights()
            path.write_text(json.dumps({
                "accepted": True,
                "setup_weight": 0.55,
                "trigger_weight": 0.30,
                "execution_weight": 0.15,
            }), encoding="utf-8")
            stat = path.stat()
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
            second = score._model_component_weights()
            self.assertEqual(first, (0.60, 0.25, 0.15))
            self.assertEqual(second, (0.55, 0.30, 0.15))
        score.OUTPUT_DIR = original_output
        score.invalidate_model_weight_cache()

    def test_locked_limit_up_is_not_buyable(self):
        frame = pd.DataFrame({
            "Open": [9.9, 10.0, 11.0],
            "High": [10.1, 10.1, 11.0],
            "Low": [9.8, 9.9, 11.0],
            "Close": [10.0, 10.0, 11.0],
            "Volume": [1000, 1000, 1000],
        })
        ok, reason = is_entry_tradeable("600000.SH", frame, 2, is_etf=False)
        self.assertFalse(ok)
        self.assertEqual(reason, "locked_limit_up")
        self.assertEqual(daily_limit_pct("300001.SZ"), 0.20)
        ok_growth, _ = is_entry_tradeable("300001.SZ", frame, 2, is_etf=False)
        self.assertTrue(ok_growth)

    def test_fast_exact_bridge_adjusts_global_prior(self):
        fast = []
        exact = []
        for index in range(6):
            ticker = f"60000{index}.SH"
            fast.append({"ticker": ticker, "entry_signal": "BUY_NOW", "samples": 20, "backtest_adjusted_score": 50 + index})
            exact.append({"ticker": ticker, "entry_signal": "BUY_NOW", "samples": 20, "backtest_adjusted_score": 55 + index})
        adjusted, metadata = bridge_global_calibration(
            [{"level": "global", "calibration_score": 50.0, "confidence": 1.0}],
            fast,
            exact,
            min_samples=10,
        )
        self.assertTrue(metadata["accepted"])
        self.assertGreater(adjusted[0]["calibration_score"], 50.0)

    def test_historical_cache_union_adds_archived_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / "600000.SH.parquet").touch()
            (cache / "000300.SH.parquet").touch()
            merged = merge_with_cached_universe(["000001.SZ"], cache)
            self.assertEqual(merged, ["000001.SZ", "600000.SH"])

    def test_fundamentals_are_gate_not_alpha_weight(self):
        self.assertEqual(MODEL_QUALITY_WEIGHT, 0.0)


if __name__ == "__main__":
    unittest.main()
'''

TEST_WORKFLOW = '''name: Regression Tests

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest]
        python-version: ['3.11']
    runs-on: ${{ matrix.os }}
    timeout-minutes: 25
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip
      - name: Install dependencies
        run: python -m pip install --upgrade pip && pip install -r requirements.txt
      - name: Compile
        run: python -m compileall -q .
      - name: Import smoke test
        env:
          MPLBACKEND: Agg
        run: python -c "import analytics, downloader, report, score, signal_lifecycle"
      - name: Unit tests
        env:
          MPLBACKEND: Agg
        run: python -m unittest discover -v
'''


write("trading_calendar.py", TRADING_CALENDAR)
write("tradeability.py", TRADEABILITY)
write("calibration_bridge.py", CALIBRATION_BRIDGE)
write("historical_universe.py", HISTORICAL_UNIVERSE)
write("test_research_integrity_v23.py", TESTS)
write(".github/workflows/tests.yml", TEST_WORKFLOW)

requirements = read("requirements.txt")
if "holidays>=" not in requirements:
    requirements = requirements.rstrip() + "\n\n# China exchange holiday-aware trading calendar\nholidays>=0.60,<1.0\n"
    write("requirements.txt", requirements)

replace_once(
    "config.py",
    'SCORING_VERSION: str = "2026-08-09-v21-output-integrity"',
    'SCORING_VERSION: str = "2026-08-09-v23-research-integrity"',
)
replace_once(
    "config.py",
    'MODEL_QUALITY_WEIGHT: Final[float] = 0.20',
    '# Fundamental quality is an execution gate/confidence input, not unvalidated alpha.\nMODEL_QUALITY_WEIGHT: Final[float] = 0.00',
)

score_block = '''_MODEL_WEIGHT_CACHE: tuple[float, float, float] | None = None
_MODEL_WEIGHT_CACHE_STATE: tuple[int, int] | None = None


def _model_weight_file_state(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return int(stat.st_mtime_ns), int(stat.st_size)


def invalidate_model_weight_cache() -> None:
    global _MODEL_WEIGHT_CACHE, _MODEL_WEIGHT_CACHE_STATE
    _MODEL_WEIGHT_CACHE = None
    _MODEL_WEIGHT_CACHE_STATE = None


def _model_component_weights() -> tuple[float, float, float]:
    global _MODEL_WEIGHT_CACHE, _MODEL_WEIGHT_CACHE_STATE
    defaults = (MODEL_SETUP_WEIGHT, MODEL_TRIGGER_WEIGHT, MODEL_EXECUTION_WEIGHT)
    path = OUTPUT_DIR / "ScoreCalibration.json"
    state = _model_weight_file_state(path)
    if _MODEL_WEIGHT_CACHE is not None and state == _MODEL_WEIGHT_CACHE_STATE:
        return _MODEL_WEIGHT_CACHE
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not bool(payload.get("accepted", False)):
            raise ValueError("calibration not accepted")
        setup = float(payload.get("setup_weight"))
        trigger = float(payload.get("trigger_weight"))
        execution = float(payload.get("execution_weight"))
        if not (0.45 <= setup <= 0.70 and 0.15 <= trigger <= 0.35 and 0.10 <= execution <= 0.25):
            raise ValueError("calibration outside guard rails")
        if abs(setup + trigger + execution - 1.0) > 1e-6:
            raise ValueError("calibration weights must sum to one")
        _MODEL_WEIGHT_CACHE = (setup, trigger, execution)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        _MODEL_WEIGHT_CACHE = defaults
    _MODEL_WEIGHT_CACHE_STATE = _model_weight_file_state(path)
    return _MODEL_WEIGHT_CACHE


def model_weight_signature() -> str:
    setup, trigger, execution = _model_component_weights()
    return f"{setup:.4f}:{trigger:.4f}:{execution:.4f}"


'''
replace_before(
    "score.py",
    "_MODEL_WEIGHT_CACHE: tuple[float, float, float] | None = None\n",
    "def _is_finite(value: Any) -> bool:\n",
    score_block,
)

insert_after_once(
    "downloader.py",
    "from network_proxy import configure_akshare_proxy_from_system\n",
    "from trading_calendar import latest_completed_trading_day, market_is_closed\n",
)
replace_before(
    "downloader.py",
    "def _latest_completed_trading_day(now: datetime | None = None) -> date:\n",
    "def _cache_has_completed_daily_bar(\n",
    '''def _latest_completed_trading_day(now: datetime | None = None) -> date:
    return latest_completed_trading_day(now)


''',
)
replace_before(
    "downloader.py",
    "def _is_a_share_market_closed(now: datetime | None = None) -> bool:\n",
    "def _load_universe_cache() -> dict[str, Any] | None:\n",
    '''def _is_a_share_market_closed(now: datetime | None = None) -> bool:
    return market_is_closed(now)


''',
)

insert_after_once(
    "analytics.py",
    "from classification import etf_tracking_key, model_classification, theme_cluster\n",
    "from calibration_bridge import bridge_global_calibration\nfrom tradeability import is_entry_tradeable\nfrom trading_calendar import trading_age_days\n",
)
replace_once(
    "analytics.py",
    '    component_calibration: dict[str, Any] = field(default_factory=dict)\n',
    '    component_calibration: dict[str, Any] = field(default_factory=dict)\n    fast_exact_bridge: dict[str, Any] = field(default_factory=dict)\n',
)
replace_once(
    "analytics.py",
    '        trading_age = max(0, len(pd.bdate_range(reported_date, today)) - 1)',
    '        trading_age = trading_age_days(reported_date)',
)
replace_once(
    "analytics.py",
    '''        if not np.isfinite(opens[entry_index]) or opens[entry_index] <= 0:
            continue
        if (
''',
    '''        if not np.isfinite(opens[entry_index]) or opens[entry_index] <= 0:
            continue
        tradeable, _tradeability_reason = is_entry_tradeable(
            ticker, enriched, entry_index, is_etf=is_etf
        )
        if not tradeable:
            continue
        if (
''',
)
replace_once(
    "analytics.py",
    '''            fast_rows = list(summary.by_ticker or [])
            for row in fast_rows:
                row.setdefault("backtest_stage", "FAST_SCREEN")
''',
    '''            fast_rows = list(summary.by_ticker or [])
            adjusted_global, bridge_metadata = bridge_global_calibration(
                summary.global_calibration,
                fast_rows,
                exact_rows,
                min_samples=BACKTEST_MIN_SAMPLES_FOR_RANKING,
            )
            summary.global_calibration = adjusted_global
            summary.fast_exact_bridge = bridge_metadata
            for row in fast_rows:
                row.setdefault("backtest_stage", "FAST_SCREEN")
''',
)
replace_once(
    "analytics.py",
    '''            # Keep full-market peer calibration; exact Top candidates only replace
            # per-ticker evidence and must not redefine the global peer prior.
''',
    '''            # Full-market peer calibration is retained, but the overlapping
            # FAST/EXACT candidates now estimate a bounded bridge correction so
            # the global prior is closer to the exact execution distribution.
''',
)

replace_once(
    "signal_lifecycle.py",
    '''    quality_action_block = quality_applicable & (
        ~result["QualityGate"]
        | result["QualityDataCompleteness"].lt(QUALITY_MIN_COMPLETENESS_FOR_ACTIONABLE)
    )
''',
    '''    style_text = _text_series(result, "Style", "").str.lower()
    cyclical_style = style_text.str.contains("周期", regex=False) | style_text.str.contains(
        "cyc", regex=False
    )
    quality_score_value = _number(
        result.get("QualityScore", pd.Series(np.nan, index=result.index)), np.nan
    )
    cyclical_quality_override = (
        cyclical_style
        & result["QualityDataCompleteness"].ge(QUALITY_MIN_COMPLETENESS_FOR_ACTIONABLE)
        & quality_score_value.ge(45.0)
    )
    result["CyclicalQualityOverride"] = cyclical_quality_override
    quality_action_block = quality_applicable & (
        result["QualityDataCompleteness"].lt(QUALITY_MIN_COMPLETENESS_FOR_ACTIONABLE)
        | (~result["QualityGate"] & ~cyclical_quality_override)
    )
''',
)

insert_after_once(
    "main.py",
    "from fundamental_data import fundamental_data_path, refresh_fundamental_data\n",
    "from historical_universe import merge_with_cached_universe\n",
)
replace_once(
    "main.py",
    '''        tickers = [normalize_ticker(ticker) for ticker in raw_tickers if ticker.strip()]
''',
    '''        current_tickers = [
            normalize_ticker(ticker) for ticker in raw_tickers if ticker.strip()
        ]
        tickers = merge_with_cached_universe(current_tickers)
        logger.info(
            "回测股票池：当前结果 %d 只 + 历史行情缓存，合计 %d 只；"
            "该方法降低但不能完全消除幸存者偏差。",
            len(current_tickers),
            len(tickers),
        )
''',
)
replace_once(
    "main.py",
    '''    summary = run_historical_backtest(unique_tickers, **backtest_kwargs)
    if getattr(summary, "insufficient_test_data", False) is True:
''',
    '''    summary = run_historical_backtest(unique_tickers, **backtest_kwargs)
    if all_results:
        summary.universe_type = "cache_plus_current_pool"
        summary.survivorship_bias_warning = True
        summary.current_pool_selection_warning = (
            "当前结果与历史缓存联合股票池，已降低但仍不能完全消除幸存者偏差"
        )
    if getattr(summary, "insufficient_test_data", False) is True:
''',
)

# Remove temporary automation files from the validated commit.
for temporary in (
    ROOT / "research_integrity_v23_migration.py",
    ROOT / ".github/workflows/apply_research_integrity_v23.yml",
):
    if temporary.exists():
        temporary.unlink()

print("research integrity v23 migration applied")
