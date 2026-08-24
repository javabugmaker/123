"""v101 strict completed-session freshness gate for canonical LIVE publication.

Provider-lag tolerance remains useful for research/recovery, but it must never
allow an older completed session to overwrite the public LIVE index after the
exchange has completed a newer session.  This module is presentation/runtime
integrity only; it does not alter scores, ranks, backtests, or eligibility.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import config as _config
from trading_calendar import latest_completed_trading_day

LIVE_PUBLICATION_VERSION = "2026-08-24-v101-current-completed-session-v1"

_PUBLICATION_FILES = (
    "AllResults.csv",
    "DecisionResults.csv",
    "Top50Mixed.csv",
    "Top50Stocks.csv",
    "Top50ETF.csv",
)


@dataclass(frozen=True)
class PublicationFreshness:
    ready: bool
    status: str
    reason: str
    expected_trading_date: str
    effective_trading_date: str
    source_dir: str
    dominant_data_asof: str
    dominant_ratio: float
    all_results_fresh_ratio: float
    file_fresh_ratios: dict[str, float]
    cache_manifest_fresh_ratio: float | None
    cache_manifest_current: int
    cache_manifest_total: int
    version: str = LIVE_PUBLICATION_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _parse_date(value: object) -> str:
    text = str(value or "").strip()
    if len(text) != 10:
        return ""
    try:
        from datetime import date

        return date.fromisoformat(text).isoformat()
    except ValueError:
        return ""


def _published_source_dir(output_dir: Path) -> Path:
    output_dir = Path(output_dir)
    latest = _read_json(output_dir / "LatestRun.json")
    relative = str(latest.get("run_dir", "") or "").strip()
    if not relative:
        return output_dir
    candidate = output_dir / relative
    try:
        candidate.resolve().relative_to(output_dir.resolve())
    except (OSError, ValueError):
        return output_dir
    if candidate.is_dir() and (candidate / "AllResults.csv").is_file():
        return candidate
    return output_dir


def _csv_date_profile(path: Path, expected_date: str) -> dict[str, Any]:
    counts: dict[str, int] = {}
    valid_rows = 0
    expected_rows = 0
    if not path.is_file():
        return {
            "rows": 0,
            "fresh_ratio": 0.0,
            "dominant_date": "",
            "dominant_ratio": 0.0,
            "counts": {},
        }
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if not str(row.get("Ticker", "") or "").strip():
                    continue
                if str(row.get("Error", "") or "").strip():
                    continue
                valid_rows += 1
                asof = _parse_date(row.get("DataAsOf", ""))
                if not asof:
                    continue
                counts[asof] = counts.get(asof, 0) + 1
                expected_rows += int(asof == expected_date)
    except (OSError, UnicodeError, csv.Error):
        return {
            "rows": 0,
            "fresh_ratio": 0.0,
            "dominant_date": "",
            "dominant_ratio": 0.0,
            "counts": {},
        }
    if counts:
        dominant_date, dominant_rows = max(counts.items(), key=lambda item: (item[1], item[0]))
    else:
        dominant_date, dominant_rows = "", 0
    return {
        "rows": valid_rows,
        "fresh_ratio": round(expected_rows / valid_rows, 4) if valid_rows else 0.0,
        "dominant_date": dominant_date,
        "dominant_ratio": round(dominant_rows / valid_rows, 4) if valid_rows else 0.0,
        "counts": dict(sorted(counts.items())),
    }


def _cache_manifest_profile(expected_date: str) -> tuple[float | None, int, int]:
    cache_dir = Path(getattr(_config, "CACHE_DIR", Path("cache")))
    candidates = list(cache_dir.glob("*/_manifest.json"))
    if not candidates:
        return None, 0, 0
    try:
        manifest = max(candidates, key=lambda path: path.stat().st_mtime_ns)
    except OSError:
        return None, 0, 0
    payload = _read_json(manifest)
    current = total = 0
    for raw in payload.values():
        if not isinstance(raw, dict) or "last_date" not in raw:
            continue
        total += 1
        current += int(_parse_date(raw.get("last_date", "")) == expected_date)
    if total <= 0:
        return None, 0, 0
    return round(current / total, 4), current, total


def _result(
    *,
    ready: bool,
    status: str,
    reason: str,
    expected_date: str,
    effective_date: str,
    source: Path,
    all_profile: dict[str, Any],
    file_ratios: dict[str, float],
    cache_profile: tuple[float | None, int, int],
) -> PublicationFreshness:
    cache_ratio, cache_current, cache_total = cache_profile
    return PublicationFreshness(
        ready=ready,
        status=status,
        reason=reason,
        expected_trading_date=expected_date,
        effective_trading_date=effective_date,
        source_dir=str(source),
        dominant_data_asof=str(all_profile.get("dominant_date", "") or ""),
        dominant_ratio=float(all_profile.get("dominant_ratio", 0.0) or 0.0),
        all_results_fresh_ratio=float(all_profile.get("fresh_ratio", 0.0) or 0.0),
        file_fresh_ratios=file_ratios,
        cache_manifest_fresh_ratio=cache_ratio,
        cache_manifest_current=cache_current,
        cache_manifest_total=cache_total,
    )


def validate_live_publication(
    output_dir: Path,
    *,
    now: datetime | None = None,
    source_dir: Path | None = None,
) -> PublicationFreshness:
    """Return whether canonical output can replace the public LIVE index now."""
    output_dir = Path(output_dir)
    source = Path(source_dir) if source_dir is not None else _published_source_dir(output_dir)
    expected_date = latest_completed_trading_day(now).isoformat()
    threshold = float(
        getattr(
            _config,
            "LIVE_PUBLICATION_MIN_FRESH_RATIO",
            getattr(_config, "DAILY_MIN_FRESH_RATIO", 0.90),
        )
    )
    daily = _read_json(source / "DailyRunSummary.json") or _read_json(
        output_dir / "DailyRunSummary.json"
    )
    latest = _read_json(output_dir / "LatestRun.json")
    summary_expected = _parse_date(daily.get("expected_trading_date", ""))
    effective_date = (
        _parse_date(daily.get("effective_trading_date", ""))
        or _parse_date(latest.get("effective_trading_date", ""))
    )

    profiles = {
        name: _csv_date_profile(source / name, expected_date)
        for name in _PUBLICATION_FILES
        if (source / name).is_file()
    }
    all_profile = profiles.get("AllResults.csv", {})
    file_ratios = {
        name: float(profile.get("fresh_ratio", 0.0) or 0.0)
        for name, profile in profiles.items()
    }
    cache_profile = _cache_manifest_profile(expected_date)

    if int(all_profile.get("rows", 0) or 0) <= 0:
        return _result(
            ready=False,
            status="MISSING_ALL_RESULTS",
            reason="AllResults.csv 缺失、为空或没有有效 DataAsOf，禁止覆盖 LIVE 页面。",
            expected_date=expected_date,
            effective_date=effective_date,
            source=source,
            all_profile=all_profile,
            file_ratios=file_ratios,
            cache_profile=cache_profile,
        )

    if summary_expected and summary_expected != expected_date:
        return _result(
            ready=False,
            status="RUN_DATE_ADVANCED",
            reason=(
                f"DAILY 启动时交易日为 {summary_expected}，当前最新完整交易日已是 "
                f"{expected_date}；本轮跨过收盘边界，必须重扫后再发布。"
            ),
            expected_date=expected_date,
            effective_date=effective_date,
            source=source,
            all_profile=all_profile,
            file_ratios=file_ratios,
            cache_profile=cache_profile,
        )

    if effective_date and effective_date != expected_date:
        return _result(
            ready=False,
            status="PROVIDER_LAG_BLOCKED",
            reason=(
                f"有效行情日仍为 {effective_date}，但当前最新完整交易日为 {expected_date}；"
                "允许研究层识别 PROVIDER_LAG，但禁止把旧交易日覆盖为 LIVE。"
            ),
            expected_date=expected_date,
            effective_date=effective_date,
            source=source,
            all_profile=all_profile,
            file_ratios=file_ratios,
            cache_profile=cache_profile,
        )

    dominant_date = str(all_profile.get("dominant_date", "") or "")
    dominant_ratio = float(all_profile.get("dominant_ratio", 0.0) or 0.0)
    all_fresh_ratio = float(all_profile.get("fresh_ratio", 0.0) or 0.0)
    if dominant_date != expected_date or dominant_ratio < threshold or all_fresh_ratio < threshold:
        return _result(
            ready=False,
            status="STALE_OR_MIXED_ALL_RESULTS",
            reason=(
                f"AllResults 主日期={dominant_date or 'missing'} ({dominant_ratio:.1%})，"
                f"当前交易日覆盖={all_fresh_ratio:.1%}；LIVE 要求主日期={expected_date} "
                f"且覆盖率≥{threshold:.0%}。"
            ),
            expected_date=expected_date,
            effective_date=effective_date or dominant_date,
            source=source,
            all_profile=all_profile,
            file_ratios=file_ratios,
            cache_profile=cache_profile,
        )

    stale_outputs: list[str] = []
    for name, profile in profiles.items():
        if name == "AllResults.csv":
            continue
        rows = int(profile.get("rows", 0) or 0)
        if rows <= 0:
            continue
        ratio = float(profile.get("fresh_ratio", 0.0) or 0.0)
        dominant = str(profile.get("dominant_date", "") or "")
        if ratio < threshold or dominant != expected_date:
            stale_outputs.append(f"{name}:{dominant or 'missing'}/{ratio:.1%}")
    if stale_outputs:
        return _result(
            ready=False,
            status="STALE_FINAL_OUTPUT",
            reason="最终榜单交易日不一致：" + "，".join(stale_outputs),
            expected_date=expected_date,
            effective_date=effective_date or expected_date,
            source=source,
            all_profile=all_profile,
            file_ratios=file_ratios,
            cache_profile=cache_profile,
        )

    return _result(
        ready=True,
        status="CURRENT_COMPLETED_SESSION",
        reason=f"LIVE 校验通过：行情与最终榜单均对齐最新完整交易日 {expected_date}。",
        expected_date=expected_date,
        effective_date=effective_date or expected_date,
        source=source,
        all_profile=all_profile,
        file_ratios=file_ratios,
        cache_profile=cache_profile,
    )


def write_publication_status(output_dir: Path, check: PublicationFreshness) -> Path:
    path = Path(output_dir) / "WebPublicationStatus.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(check.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
    return path
