"""InstitutionScanner v74 daily-pipeline settlement and recovery facade.

The transactional implementation remains in ``daily_pipeline_core``. v53
hardens provider settlement-date semantics; v74 additionally installs PID-aware
recovery of unfinished outer DAILY transactions before a new run can start.
Mixed-date/materially stale universes remain fail-closed.
"""

from __future__ import annotations

import csv
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import config as _config
import daily_pipeline_core as _core
import daily_recovery_v74 as _daily_recovery
from trading_calendar import is_trading_day
from web_report import maybe_publish_canonical_report

_daily_recovery.install()

_LEGACY_CSV_PROFILE = _core._csv_profile
_LEGACY_QUALITY_GATE_ERRORS = _core._quality_gate_errors
_LEGACY_WRITE_MANIFEST = _core._write_manifest
_LEGACY_ACTIVATE_RUN = _core._activate_run


def _parse_iso_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _trading_day_gap(older: date, newer: date) -> int:
    if older > newer:
        return -1
    gap = 0
    cursor = older
    while cursor < newer:
        cursor += timedelta(days=1)
        if is_trading_day(cursor):
            gap += 1
    return gap


def _data_asof_distribution(path: Path) -> tuple[dict[str, int], int]:
    counts: dict[str, int] = {}
    valid_rows = 0
    if not path.exists():
        return counts, valid_rows
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                if not str(row.get("Ticker", "")).strip():
                    continue
                if str(row.get("Error", "") or "").strip():
                    continue
                valid_rows += 1
                value = str(row.get("DataAsOf", "") or "").strip()
                if _parse_iso_date(value) is not None:
                    counts[value] = counts.get(value, 0) + 1
    except (OSError, UnicodeError, csv.Error):
        return {}, 0
    return counts, valid_rows


def _resolve_profile_date(
    path: Path,
    expected_date: str,
    legacy_profile: dict[str, object],
) -> dict[str, object]:
    counts, valid_rows = _data_asof_distribution(path)
    expected = _parse_iso_date(expected_date)
    expected_ratio = float(legacy_profile.get("fresh_ratio", 0.0) or 0.0)
    threshold = float(
        getattr(
            _config,
            "DAILY_MIN_COHERENT_DATA_DATE_RATIO",
            _core.DAILY_MIN_FRESH_RATIO,
        )
    )
    max_lag = int(getattr(_config, "DAILY_MAX_PROVIDER_LAG_TRADING_DAYS", 1))

    if counts:
        dominant_date, dominant_rows = max(
            counts.items(),
            key=lambda item: (item[1], _parse_iso_date(item[0]) or date.min),
        )
    else:
        dominant_date, dominant_rows = "", 0
    dominant_ratio = dominant_rows / valid_rows if valid_rows else 0.0

    resolution: dict[str, object] = {
        "calendar_expected_date": expected_date,
        "effective_date": expected_date,
        "status": "UNKNOWN",
        "accepted": False,
        "lag_trading_days": 0,
        "calendar_expected_ratio": round(expected_ratio, 4),
        "dominant_data_asof": dominant_date,
        "dominant_ratio": round(dominant_ratio, 4),
        "data_asof_counts": dict(sorted(counts.items())),
        "reason": "",
    }
    if expected is None:
        resolution.update(
            status="INVALID_EXPECTED_DATE",
            reason="日历预期交易日格式无效",
        )
        return resolution
    if expected_ratio >= float(_core.DAILY_MIN_FRESH_RATIO):
        resolution.update(
            status="ON_TIME",
            accepted=True,
            reason="数据已覆盖日历最新完整交易日",
        )
        return resolution

    dominant = _parse_iso_date(dominant_date)
    if dominant is None:
        resolution.update(
            status="MISSING_DATA_ASOF",
            reason="有效行情缺少可确认的 DataAsOf 日期",
        )
        return resolution
    if dominant > expected:
        resolution.update(
            status="FUTURE_DATA_DATE",
            effective_date=dominant_date,
            reason=f"行情日期 {dominant_date} 晚于日历预期 {expected_date}",
        )
        return resolution

    lag = _trading_day_gap(dominant, expected)
    resolution["effective_date"] = dominant_date
    resolution["lag_trading_days"] = lag
    if dominant_ratio < threshold:
        resolution.update(
            status="MIXED_DATA_DATES",
            reason=(
                f"行情日期分布不一致：主日期 {dominant_date} 仅覆盖 "
                f"{dominant_ratio:.1%}，低于 {threshold:.0%}"
            ),
        )
        return resolution
    if lag <= max_lag:
        resolution.update(
            status="PROVIDER_LAG" if lag else "COHERENT_CURRENT_DATE",
            accepted=True,
            reason=(
                f"数据源统一落后 {lag} 个交易日，使用最新一致完整日 {dominant_date}"
                if lag
                else f"使用一致完整交易日 {dominant_date}"
            ),
        )
        return resolution

    resolution.update(
        status="STALE_DATA",
        reason=(
            f"数据源主日期 {dominant_date} 落后日历预期 "
            f"{expected_date} 共 {lag} 个交易日"
        ),
    )
    return resolution


def _csv_profile(path: Path, expected_date: str) -> dict[str, object]:
    """Profile freshness against calendar date, then a safe provider settlement date."""
    calendar_profile = _LEGACY_CSV_PROFILE(path, expected_date)
    resolution = _resolve_profile_date(path, expected_date, calendar_profile)
    effective_date = str(resolution.get("effective_date", expected_date) or expected_date)
    if bool(resolution.get("accepted", False)) and effective_date != expected_date:
        profile = _LEGACY_CSV_PROFILE(path, effective_date)
    else:
        profile = calendar_profile

    profile["calendar_expected_date"] = expected_date
    profile["effective_trading_date"] = effective_date
    profile["market_data_date_status"] = str(resolution.get("status", "UNKNOWN"))
    profile["market_data_lag_trading_days"] = int(
        resolution.get("lag_trading_days", 0) or 0
    )
    profile["calendar_expected_fresh_ratio"] = float(
        resolution.get("calendar_expected_ratio", 0.0) or 0.0
    )
    profile["dominant_data_asof"] = str(resolution.get("dominant_data_asof", ""))
    profile["dominant_data_asof_ratio"] = float(
        resolution.get("dominant_ratio", 0.0) or 0.0
    )
    raw_counts = resolution.get("data_asof_counts", {})
    profile["data_asof_counts"] = raw_counts if isinstance(raw_counts, dict) else {}
    profile["market_data_date_reason"] = str(resolution.get("reason", "") or "")
    profile["market_data_date_accepted"] = bool(resolution.get("accepted", False))
    return profile


def _quality_gate_errors(
    scan_profile: dict[str, object],
    previous_summary: dict[str, object],
    *,
    quality_gates: bool,
) -> list[str]:
    errors = _LEGACY_QUALITY_GATE_ERRORS(
        scan_profile,
        previous_summary,
        quality_gates=quality_gates,
    )
    if not quality_gates:
        return errors
    if bool(scan_profile.get("market_data_date_accepted", False)):
        return errors
    reason = str(scan_profile.get("market_data_date_reason", "") or "").strip()
    if reason:
        errors.insert(0, "行情交易日一致性失败：" + reason)
    return errors


def _write_manifest(*args: Any, **kwargs: Any) -> dict[str, object]:
    payload = _LEGACY_WRITE_MANIFEST(*args, **kwargs)
    scan_profile = kwargs.get("scan_profile", {})
    if not isinstance(scan_profile, dict):
        scan_profile = {}
    payload.update(
        {
            "effective_trading_date": scan_profile.get(
                "effective_trading_date", payload.get("expected_trading_date", "")
            ),
            "market_data_date_status": scan_profile.get(
                "market_data_date_status", "UNKNOWN"
            ),
            "market_data_lag_trading_days": int(
                scan_profile.get("market_data_lag_trading_days", 0) or 0
            ),
            "calendar_expected_fresh_ratio": float(
                scan_profile.get("calendar_expected_fresh_ratio", 0.0) or 0.0
            ),
            "data_asof_distribution": (
                scan_profile.get("data_asof_counts", {})
                if isinstance(scan_profile.get("data_asof_counts", {}), dict)
                else {}
            ),
            "market_data_date_reason": str(
                scan_profile.get("market_data_date_reason", "") or ""
            ),
        }
    )
    root = kwargs.get("result_dir") or _core.OUTPUT_DIR
    _core._atomic_write_json(Path(root) / "DailyRunSummary.json", payload)
    return payload


def _activate_run(
    pipeline_run_id: str,
    run_dir: Path,
    payload: dict[str, object],
) -> None:
    _LEGACY_ACTIVATE_RUN(pipeline_run_id, run_dir, payload)
    path = _core.OUTPUT_DIR / "LatestRun.json"
    try:
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        current = {}
    if not isinstance(current, dict):
        current = {}
    current.update(
        {
            "effective_trading_date": payload.get("effective_trading_date", ""),
            "market_data_date_status": payload.get("market_data_date_status", ""),
            "market_data_lag_trading_days": payload.get(
                "market_data_lag_trading_days", 0
            ),
        }
    )
    _core._atomic_write_json(path, current)
    maybe_publish_canonical_report(
        Path(_core.OUTPUT_DIR),
        logger=logging.getLogger("institution_scanner"),
        reason="daily-complete",
    )


_core._csv_profile = _csv_profile
_core._quality_gate_errors = _quality_gate_errors
_core._write_manifest = _write_manifest
_core._activate_run = _activate_run
_core.DAILY_RECOVERY_INTEGRITY_VERSION = (
    "2026-08-19-v74-pid-aware-outer-transaction-recovery-v1"
)

if __name__ == "__main__":
    raise SystemExit(_core.main())

sys.modules[__name__] = _core
