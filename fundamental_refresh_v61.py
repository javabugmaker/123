"""v70 fundamental refresh freshness guard.

The stable AkShare implementation merges old rows with whatever the current
provider request returns. That is useful for continuity, but freshness metadata
must represent the hard financial inputs used by the quality gate rather than
mere row presence. A row returned only by the institution-coverage endpoint can
otherwise make stale ROE/profit/margin data look freshly updated.

The combined CSV is always preserved. Its metadata timestamp advances only when
at least 80% of requested stocks receive current hard financial evidence:
ROE + three annual profit values, plus gross margin for GENERAL/CYCLICAL
industries where the model requires the margin gate. Institution coverage is
counted separately for diagnostics and never substitutes for hard finance.
"""

from __future__ import annotations

import math
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

import fundamental_data as _data

_LEGACY_REFRESH = _data.refresh_fundamental_data
_LEGACY_FETCH_ROW = _data._fetch_fundamental_row
_REFRESH_GUARD_LOCK = threading.Lock()
_INSTALLED = False


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError:
        return None


def _restore_bytes(path: Path, payload: bytes | None) -> None:
    if payload is None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _requested_stock_count(args: tuple[Any, ...], kwargs: dict[str, Any]) -> int:
    values = kwargs.get("tickers")
    if values is None and args:
        values = args[0]
    if values is None:
        return 0
    try:
        normalized = {
            _data.normalize_ticker(value)
            for value in values
            if str(value).strip()
        }
    except TypeError:
        return 0
    return sum(
        1
        for ticker in normalized
        if ticker
        and not ticker.split(".", 1)[0].startswith(
            ("15", "16", "50", "51", "56", "58")
        )
    )


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _hard_financial_row_is_current(row: dict[str, Any]) -> bool:
    """Match the profile-specific hard financial evidence used by quality gate."""
    from fundamental_quality import quality_profile

    if not _finite(row.get("ROE")):
        return False
    if not all(
        _finite(row.get(column))
        for column in ("NetProfitY1", "NetProfitY2", "NetProfitY3")
    ):
        return False
    profile = quality_profile(str(row.get("Industry", "") or ""))
    if profile in {"GENERAL", "CYCLICAL"} and not _finite(row.get("GrossMargin")):
        return False
    return True


def _holding_row_is_current(row: dict[str, Any]) -> bool:
    periods = row.get("InstitutionHoldingPeriods")
    trend = str(row.get("InstitutionHoldingTrend", "") or "").strip().lower()
    return _finite(periods) and float(periods) >= 2 and trend in {
        "increasing",
        "not_increasing",
    }


def refresh_fundamental_data(*args: Any, **kwargs: Any) -> Path:
    """Keep metadata stale unless current hard-financial coverage is broad."""
    with _REFRESH_GUARD_LOCK:
        metadata_path = Path(_data._META_PATH)
        metadata_before = _read_bytes(metadata_path)
        expected_rows = _requested_stock_count(args, kwargs)
        returned_rows = 0
        hard_financial_rows = 0
        holding_rows = 0

        def counting_fetch(*fetch_args: Any, **fetch_kwargs: Any):
            nonlocal returned_rows, hard_financial_rows, holding_rows
            row = _LEGACY_FETCH_ROW(*fetch_args, **fetch_kwargs)
            if row is not None:
                returned_rows += 1
                if _hard_financial_row_is_current(row):
                    hard_financial_rows += 1
                if _holding_row_is_current(row):
                    holding_rows += 1
            return row

        _data._fetch_fundamental_row = counting_fetch
        try:
            result = _LEGACY_REFRESH(*args, **kwargs)
        finally:
            _data._fetch_fundamental_row = _LEGACY_FETCH_ROW

        metadata_after = _read_bytes(metadata_path)
        metadata_changed = metadata_after != metadata_before
        if not metadata_changed:
            return result

        minimum_coverage = float(
            getattr(_data, "_CACHE_COMPLETENESS_THRESHOLD", 0.80)
        )
        financial_coverage = (
            hard_financial_rows / expected_rows if expected_rows > 0 else 1.0
        )
        holding_coverage = (
            holding_rows / expected_rows if expected_rows > 0 else 1.0
        )
        insufficient_refresh = (
            expected_rows > 0
            and financial_coverage + 1e-12 < minimum_coverage
        )
        if insufficient_refresh:
            _restore_bytes(metadata_path, metadata_before)
            _data.logger.warning(
                "AKShare 本轮硬财务覆盖率仅 %.1f%%（%d/%d，要求至少 %.0f%%）；"
                "返回行=%d，机构覆盖有效率=%.1f%%。保留已合并数据但不刷新时效戳，"
                "后续扫描仍会重试。",
                financial_coverage * 100.0,
                hard_financial_rows,
                expected_rows,
                minimum_coverage * 100.0,
                returned_rows,
                holding_coverage * 100.0,
            )
        else:
            _data.logger.info(
                "AKShare 本轮 freshness 验证通过：硬财务 %.1f%%（%d/%d），"
                "机构覆盖有效率 %.1f%%。",
                financial_coverage * 100.0,
                hard_financial_rows,
                expected_rows,
                holding_coverage * 100.0,
            )
        return result


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _data.refresh_fundamental_data = refresh_fundamental_data
    _INSTALLED = True


install()
