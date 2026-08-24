"""v101 overlay that separates provider-lag research from LIVE publication.

The v53/v74 daily facade can accept one coherent provider-lag session for
research/recovery.  That remains useful evidence, but a canonical DAILY run
must not publish it as the current public report after a newer session has
completed.  Install this overlay after ``daily_pipeline`` installs its legacy
compatibility hooks.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from trading_calendar import latest_completed_trading_day

LIVE_PUBLICATION_INTEGRITY_VERSION = (
    "2026-08-24-v101-current-completed-session-close-boundary-retry-v1"
)


def _live_completed_session_error(scan_profile: dict[str, object]) -> str:
    current = latest_completed_trading_day().isoformat()
    calendar_expected = str(
        scan_profile.get("calendar_expected_date", "")
        or scan_profile.get("expected_trading_date", "")
        or ""
    ).strip()
    effective = str(scan_profile.get("effective_trading_date", "") or "").strip()
    if calendar_expected and calendar_expected != current:
        return (
            f"运行期间最新完整交易日已从 {calendar_expected} 推进到 {current}；"
            "本轮扫描快照已过期，必须从最新交易日重新扫描。"
        )
    if effective and effective != current:
        return (
            f"TickFlow 有效行情日仍为 {effective}，当前最新完整交易日为 {current}；"
            "PROVIDER_LAG 可保留作研究诊断，但不得进入 DAILY LIVE 发布。"
        )
    return ""


def install(core: Any) -> None:
    if getattr(core, "_LIVE_PUBLICATION_V101_INSTALLED", False):
        return

    legacy_quality_gate_errors = core._quality_gate_errors
    legacy_final_output_errors = core._final_output_errors
    legacy_write_manifest = core._write_manifest
    legacy_run_daily_pipeline = core.run_daily_pipeline

    def quality_gate_errors(
        scan_profile: dict[str, object],
        previous_summary: dict[str, object],
        *,
        quality_gates: bool,
    ) -> list[str]:
        errors = legacy_quality_gate_errors(
            scan_profile,
            previous_summary,
            quality_gates=quality_gates,
        )
        if not quality_gates:
            return errors
        live_error = _live_completed_session_error(scan_profile)
        if live_error:
            errors.insert(0, "LIVE 交易日闸门失败：" + live_error)
        return errors

    def final_output_errors(
        scan_profile: dict[str, object],
        profiles: dict[str, dict[str, object]],
        *,
        quality_gates: bool,
    ) -> list[str]:
        errors = legacy_final_output_errors(
            scan_profile,
            profiles,
            quality_gates=quality_gates,
        )
        if not quality_gates:
            return errors
        live_error = _live_completed_session_error(scan_profile)
        if live_error:
            errors.insert(0, "LIVE 发布前复核失败：" + live_error)
        return errors

    def write_manifest(*args: Any, **kwargs: Any) -> dict[str, object]:
        payload = legacy_write_manifest(*args, **kwargs)
        scan_profile = kwargs.get("scan_profile", {})
        if not isinstance(scan_profile, dict):
            scan_profile = {}
        live_error = _live_completed_session_error(scan_profile)
        payload.update(
            {
                "live_publication_expected_trading_date": (
                    latest_completed_trading_day().isoformat()
                ),
                "live_publication_ready": not bool(live_error),
                "live_publication_status": (
                    "CURRENT_COMPLETED_SESSION"
                    if not live_error
                    else "BLOCKED_STALE_SESSION"
                ),
                "live_publication_reason": (
                    live_error or "当前结果对齐最新完整交易日"
                ),
                "live_publication_integrity_version": (
                    LIVE_PUBLICATION_INTEGRITY_VERSION
                ),
            }
        )
        root = Path(kwargs.get("result_dir") or core.OUTPUT_DIR)
        core._atomic_write_json(root / "DailyRunSummary.json", payload)
        return payload

    def run_daily_pipeline(
        *,
        data_source: str = "tickflow",
        workers: int | None = None,
        refresh_fundamentals: bool = False,
        backtest_mode: str = "fast",
        quality_gates: bool = True,
    ) -> int:
        start_session = latest_completed_trading_day()
        code = legacy_run_daily_pipeline(
            data_source=data_source,
            workers=workers,
            refresh_fundamentals=refresh_fundamentals,
            backtest_mode=backtest_mode,
            quality_gates=quality_gates,
        )
        end_session = latest_completed_trading_day()
        if code != 0 and end_session > start_session:
            logging.getLogger("institution_scanner.daily").warning(
                "DAILY crossed the completed-session boundary (%s -> %s); "
                "automatically restarting once so scan/backtest/publication share the new date.",
                start_session.isoformat(),
                end_session.isoformat(),
            )
            return legacy_run_daily_pipeline(
                data_source=data_source,
                workers=workers,
                refresh_fundamentals=refresh_fundamentals,
                backtest_mode=backtest_mode,
                quality_gates=quality_gates,
            )
        return code

    core._quality_gate_errors = quality_gate_errors
    core._final_output_errors = final_output_errors
    core._write_manifest = write_manifest
    core.run_daily_pipeline = run_daily_pipeline
    core.LIVE_PUBLICATION_INTEGRITY_VERSION = LIVE_PUBLICATION_INTEGRITY_VERSION
    core._LIVE_PUBLICATION_V101_INSTALLED = True
