"""Out-of-process probe for ``daily_pipeline_core`` facts that cannot be seen
from inside the assembled runtime.

Two reasons this runs as a subprocess rather than as fixtures:

* ``daily_pipeline_core`` is mutated in place by three overlays.  Anything that
  needs the *original* implementation must be observed before ``daily_pipeline``
  is imported, and pytest offers no ordering guarantee across test modules.
* importing the facade installs overlays process-wide, which would poison every
  later assertion in the same session.

Order matters inside this file: the "clean core" scenarios run first, the
assembly scenarios second (they import the facade and rebind the module).

Prints one JSON object on stdout.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
from typing import Any
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPORT: dict[str, Any] = {}


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------------------
# Part 1 -- clean core (before any overlay is installed)
# --------------------------------------------------------------------------------------
def run_clean_core() -> None:
    import daily_pipeline_core as core

    REPORT["pipeline_version"] = str(core.PIPELINE_VERSION)

    with tempfile.TemporaryDirectory() as raw:
        tmp = pathlib.Path(raw)
        expected = "2026-09-15"

        # Without the Quality* columns at all -- the case where the two call
        # sites disagree about the default.
        bare = tmp / "bare.csv"
        _write(
            bare,
            "Ticker,AssetType,Error,DataAsOf,RunId\n"
            "600000.SH,,," + expected + ",r1\n"
            "600001.SH,,," + expected + ",r1\n"
            "600002.SH,,," + expected + ",r1\n",
        )
        # Same frame, but the column is present and explicitly true.
        flagged = tmp / "flagged.csv"
        _write(
            flagged,
            "Ticker,AssetType,Error,DataAsOf,RunId,QualityApplicable,QualityGate,"
            "QualityHardDataComplete\n"
            "600000.SH,,," + expected + ",r1,true,true,true\n"
            "600001.SH,,," + expected + ",r1,true,true,true\n"
            "600002.SH,,," + expected + ",r1,true,true,true\n",
        )

        bare_profile = core._csv_profile(bare, expected)
        flagged_profile = core._csv_profile(flagged, expected)
        bare_snapshot = core._decision_snapshot(bare)
        flagged_snapshot = core._decision_snapshot(flagged)

        REPORT["quality_default_split"] = {
            "bare_profile_applicable_stocks": bare_profile["quality_applicable_stocks"],
            "bare_profile_valid_stocks": bare_profile["valid_stocks"],
            "bare_snapshot_applicable": {
                ticker: row["quality_applicable"] for ticker, row in bare_snapshot.items()
            },
            "flagged_profile_applicable_stocks": flagged_profile["quality_applicable_stocks"],
            "flagged_snapshot_applicable": {
                ticker: row["quality_applicable"] for ticker, row in flagged_snapshot.items()
            },
        }

    # --- cold_start: the redundant expression vs its reduced form ---
    version = str(core.PIPELINE_VERSION)
    cases = {
        "empty": "",
        "stale": "0.0.0-ancient",
        "same": version,
        "missing": None,
    }
    observed: dict[str, bool] = {}
    reduced: dict[str, bool] = {}
    for label, value in cases.items():
        summary = {} if value is None else {"pipeline_version": value}
        observed[label] = bool(core._cache_health(summary, 0.5, 100)["cold_start"])
        reduced[label] = str(value or "") != version
    REPORT["cold_start"] = {
        "observed": observed,
        "reduced_form": reduced,
        "equivalent": observed == reduced,
        "pipeline_version_nonempty": bool(version),
    }

    # --- _read_json already defends against non-dict top level ---
    with tempfile.TemporaryDirectory() as raw:
        tmp = pathlib.Path(raw)
        for label, text in (("list", "[1, 2, 3]"), ("null", "null"), ("string", '"x"')):
            target = tmp / f"{label}.json"
            _write(target, text)
            REPORT.setdefault("read_json", {})[label] = core._read_json(target)

    # --- the two publication gates, on the clean core ---
    # Both are rebound by overlays, so their original semantics are only
    # observable before ``daily_pipeline`` is imported.
    import config as config_module

    uni_min = int(config_module.DAILY_MIN_UNIVERSE_TOTAL)
    stock_min = int(config_module.DAILY_MIN_STOCK_COUNT)
    etf_min = int(config_module.DAILY_MIN_ETF_COUNT)
    stocks = max(stock_min * 2, 10)
    etfs = max(etf_min * 2, 10)
    rows = max(uni_min * 2, stocks + etfs)
    healthy: dict[str, object] = {
        "rows": rows,
        "stocks": stocks,
        "etfs": etfs,
        "valid_stocks": stocks,
        "valid_etfs": etfs,
        "valid_stock_ratio": 1.0,
        "valid_etf_ratio": 1.0,
        "fresh_ratio": 1.0,
        "run_ids": ["r1"],
    }
    empty_profile: dict[str, object] = {
        "rows": 0,
        "stocks": 0,
        "etfs": 0,
        "valid_stocks": 0,
        "valid_etfs": 0,
        "valid_stock_ratio": 0.0,
        "valid_etf_ratio": 0.0,
        "fresh_ratio": 0.0,
        "run_ids": [],
    }
    floor = float(config_module.DAILY_RELATIVE_UNIVERSE_FLOOR)
    dropped = dict(healthy)
    dropped["rows"] = int(1000 * floor) - 1 if 1000 * floor > 1 else 0

    REPORT["quality_gate_errors"] = {
        "healthy": core._quality_gate_errors(healthy, {}, quality_gates=True),
        "empty": core._quality_gate_errors(empty_profile, {}, quality_gates=True),
        "empty_gates_off": core._quality_gate_errors(empty_profile, {}, quality_gates=False),
        "relative_drop": core._quality_gate_errors(
            dropped, {"universe": {"rows": 1000, "stocks": 900, "etfs": 100}}, quality_gates=True
        ),
    }

    complete_split = {
        name: {"rows": 50, "fresh_ratio": 1.0, "run_ids": ["r1"]}
        for name in core.FINAL_OUTPUTS
    }
    REPORT["final_output_errors"] = {
        "consistent": core._final_output_errors(healthy, complete_split, quality_gates=True),
        "missing_file": core._final_output_errors(
            healthy, {name: {"rows": 0} for name in core.FINAL_OUTPUTS}, quality_gates=True
        ),
        "stale_freshness": core._final_output_errors(
            healthy,
            {
                name: {"rows": 50, "fresh_ratio": 0.0, "run_ids": ["r1"]}
                for name in core.FINAL_OUTPUTS
            },
            quality_gates=True,
        ),
        "run_id_mismatch": core._final_output_errors(
            healthy,
            {
                name: {"rows": 50, "fresh_ratio": 1.0, "run_ids": ["other"]}
                for name in core.FINAL_OUTPUTS
            },
            quality_gates=True,
        ),
        "gates_off": core._final_output_errors(
            healthy, {name: {"rows": 0} for name in core.FINAL_OUTPUTS}, quality_gates=False
        ),
    }

    # --- rollback: which exception types escape ---
    with tempfile.TemporaryDirectory() as raw:
        tmp = pathlib.Path(raw)
        canonical = tmp / "canonical"
        _write(canonical / "Top50Mixed.csv", "Ticker\n600000.SH\n")
        tx_dir = tmp / "tx" / "run-1"
        _write(tx_dir / "state.json", '{"existing": []}')

        original_dir = core.OUTPUT_DIR
        core.OUTPUT_DIR = canonical
        try:
            outcomes: dict[str, Any] = {}
            for label, error in (
                ("file_not_found", FileNotFoundError("gone")),
                ("permission", PermissionError("locked")),
                ("os_error", OSError("busy")),
            ):
                try:
                    with mock.patch.object(pathlib.Path, "unlink", side_effect=error):
                        core._rollback_transaction(tx_dir, set())
                    outcomes[label] = "swallowed"
                except Exception as raised:  # noqa: BLE001 - we are classifying escapes
                    outcomes[label] = type(raised).__name__
            REPORT["rollback_escape"] = outcomes
        finally:
            core.OUTPUT_DIR = original_dir


# --------------------------------------------------------------------------------------
# Part 2 -- assembled runtime
# --------------------------------------------------------------------------------------
def _owner(func: object) -> str:
    code = getattr(func, "__code__", None)
    return pathlib.Path(code.co_filename).name if code is not None else "<none>"


def _next_layer(func: object) -> object | None:
    """Previous implementation, but only if this layer actually references it.

    Two lookup shapes exist and both must be handled:

    * overlays defined inside ``install()`` (``daily_live_freshness_v101``)
      hold the previous implementation in a closure cell -> ``co_freevars``;
    * overlays defined at module scope (``daily_pipeline``,
      ``daily_recovery_v74``) hold it as a module global -> ``co_names``.

    The name is matched against ``co_freevars`` / ``co_names`` rather than
    against ``__globals__``: the latter only proves the module captured a
    reference, so deleting the *call* would still resolve a predecessor and
    the chain would stay intact.  Reverse validation case C depends on this
    distinction.
    """
    code = getattr(func, "__code__", None)
    if code is None:
        return None
    referenced = set(getattr(code, "co_freevars", ())) | set(getattr(code, "co_names", ()))
    candidates = {name for name in referenced if "legacy" in name.lower()}
    for name in sorted(candidates):
        for source in (getattr(func, "__closure__", None) or (),):
            for free, cell in zip(getattr(code, "co_freevars", ()), source):
                if free == name:
                    try:
                        value = cell.cell_contents
                    except ValueError:
                        continue
                    if callable(value):
                        return value
        value = (getattr(func, "__globals__", None) or {}).get(name)
        if callable(value):
            return value
    return None


def run_assembly() -> None:
    import daily_pipeline  # noqa: F401  - installing the overlays is the point
    import daily_pipeline_core as core

    REPORT["identity"] = {
        "facade_is_core": daily_pipeline is core,
        "sys_modules_entry_is_core": sys.modules["daily_pipeline"] is core,
        "facade_file": pathlib.Path(daily_pipeline.__file__).name,
    }

    chains: dict[str, list[str]] = {}
    for name in (
        "run_daily_pipeline",
        "_write_manifest",
        "_quality_gate_errors",
        "_final_output_errors",
        "_csv_profile",
        "_activate_run",
        "_begin_transaction",
    ):
        func = getattr(core, name)
        chain = [_owner(func)]
        current = func
        for _ in range(6):
            nxt = _next_layer(current)
            if nxt is None:
                break
            owner = _owner(nxt)
            if owner == chain[-1]:
                break
            chain.append(owner)
            current = nxt
        chains[name] = chain
    REPORT["chains"] = chains

    REPORT["versions"] = {
        "recovery": str(getattr(core, "DAILY_RECOVERY_INTEGRITY_VERSION", "")),
        "live_publication": str(getattr(core, "LIVE_PUBLICATION_INTEGRITY_VERSION", "")),
        "v101_installed": bool(getattr(core, "_LIVE_PUBLICATION_V101_INSTALLED", False)),
    }


def main() -> int:
    try:
        run_clean_core()
    except Exception as error:  # noqa: BLE001 - report, do not abort the whole probe
        REPORT["clean_core_error"] = f"{type(error).__name__}: {error}"
    try:
        run_assembly()
    except Exception as error:  # noqa: BLE001
        REPORT["assembly_error"] = f"{type(error).__name__}: {error}"
    json.dump(REPORT, sys.stdout, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
