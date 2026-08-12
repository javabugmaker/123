from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "report.py"
text = TARGET.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    text = text.replace(old, new, 1)


# Historical ScanResult fixtures predate the explicit hard-gate contract.  If
# none of the hard-gate keys were evaluated, preserve their old PassedFilters
# semantics.  Current scans explicitly populate these keys, so their hard gate
# remains authoritative and is never relaxed by this compatibility path.
replace_once(
    '''def _failed_filter_names(result: ScanResult, keys: tuple[str, ...]) -> list[str]:\n    names: list[str] = []\n    for key in keys:\n        if result.is_etf and key in {"min_price", "min_market_cap"}:\n            continue\n        if not bool(result.filter_details.get(key, False)):\n            names.append(key)\n    return names\n\n\ndef _results_to_dataframe(results: list[ScanResult]) -> pd.DataFrame:\n''',
    '''def _hard_gate_evaluated(result: ScanResult) -> bool:\n    return any(key in result.filter_details for key in _HARD_GATE_FILTER_KEYS)\n\n\ndef _hard_gate_passed(result: ScanResult) -> bool:\n    if _hard_gate_evaluated(result):\n        return bool(result.universe_eligible)\n    return bool(result.passed_filters)\n\n\ndef _failed_filter_names(result: ScanResult, keys: tuple[str, ...]) -> list[str]:\n    if (\n        keys == _HARD_GATE_FILTER_KEYS\n        and not _hard_gate_evaluated(result)\n        and result.passed_filters\n    ):\n        return []\n    names: list[str] = []\n    for key in keys:\n        if result.is_etf and key in {"min_price", "min_market_cap"}:\n            continue\n        if not bool(result.filter_details.get(key, False)):\n            names.append(key)\n    return names\n\n\ndef _results_to_dataframe(results: list[ScanResult]) -> pd.DataFrame:\n''',
    "legacy hard-gate source helper",
)

replace_once(
    '''                "UniverseEligible": r.universe_eligible,\n                "HardGatePassed": r.universe_eligible,\n''',
    '''                "UniverseEligible": _hard_gate_passed(r),\n                "HardGatePassed": _hard_gate_passed(r),\n''',
    "hard-gate export fallback",
)

# The ranking policy still has a defensive compatibility fallback for frames
# produced outside _results_to_dataframe.  Real hard failures carry explicit
# failed names and therefore cannot use it.
replace_once(
    '''        hard_ok = _truthy(hard_value)\n        if not hard_ok:\n            failed_names = _clean_group_key(row.get("HardGateFailedNames", ""))\n            hard_reason = (\n                f"硬准入失败：{failed_names}" if failed_names else "硬准入条件未通过"\n            )\n            reason = f"{reason}；{hard_reason}" if reason else hard_reason\n        eligibility.append(bool(eligible) and hard_ok)\n''',
    '''        hard_ok = _truthy(hard_value)\n        failed_names = _clean_group_key(row.get("HardGateFailedNames", ""))\n        legacy_combined_pass = (\n            not hard_ok\n            and not failed_names\n            and _truthy(row.get("PassedFilters", False))\n        )\n        if legacy_combined_pass:\n            hard_ok = True\n        if not hard_ok:\n            hard_reason = (\n                f"硬准入失败：{failed_names}" if failed_names else "硬准入条件未通过"\n            )\n            reason = f"{reason}；{hard_reason}" if reason else hard_reason\n        eligibility.append(bool(eligible) and hard_ok)\n''',
    "defensive research-policy compatibility",
)

TARGET.write_text(text, encoding="utf-8")
