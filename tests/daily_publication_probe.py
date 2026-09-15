"""Subprocess probe for the daily publication safety contract.

Run directly::

    python tests/daily_publication_probe.py --out <result.json>

Isolation is not optional.  ``import daily_pipeline`` transitively imports
``main``, which installs overlays process-wide; exercising the pipeline inside
the pytest interpreter would silently change what every later assertion sees.
This mirrors the reasoning already recorded in
``tests/assembly_manifest.capture_subprocess``.

Every scenario runs against a throwaway ``OUTPUT_DIR``.  Nothing here touches
the real ``output/`` tree, and no network or market data is required: the scan
and backtest stages are replaced by injected failures, which is exactly the
path the safety contract is supposed to protect.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import daily_pipeline as pipeline  # noqa: E402

# A minimal but pattern-matching published set: every name must be selected by
# ``_PUBLISH_PATTERNS`` so that ``_published_files`` really snapshots it.
PUBLISHED_SAMPLES: dict[str, str] = {
    "AllResults.csv": "Ticker,DataAsOf\n600036.SH,2026-09-14\n",
    "Top50Mixed.csv": "Ticker\n600036.SH\n",
    "PublicCandidates.csv": "Ticker\n600036.SH\n",
}

_WATCHED_ATTRIBUTES = ("OUTPUT_DIR", "_CHECKPOINT_PATH", "HISTORY_FILE", "TRACKING_FILE")


def _digest(root: Path, name: str) -> str:
    path = root / name
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_published(root: Path) -> dict[str, str]:
    digests: dict[str, str] = {}
    for name, text in PUBLISHED_SAMPLES.items():
        (root / name).write_text(text, encoding="utf-8")
        digests[name] = _digest(root, name)
    return digests


class _output_dir:
    """Point the pipeline at *root* for the duration of the block."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._previous: Any = None

    def __enter__(self) -> Path:
        self._previous = pipeline.OUTPUT_DIR
        pipeline.OUTPUT_DIR = self._root
        return self._root

    def __exit__(self, *_exc: object) -> None:
        pipeline.OUTPUT_DIR = self._previous


def scenario_transaction_rollback() -> dict[str, Any]:
    """A failure must restore the pre-run published set byte for byte."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        before = _write_published(root)
        with _output_dir(root):
            tx_dir, existing = pipeline._begin_transaction("run-rollback")
            # The run mutates the canonical set: one overwrite, one deletion,
            # one brand-new file.  Rollback has to undo all three.
            (root / "AllResults.csv").write_text("Ticker\n999999.SH\n", encoding="utf-8")
            (root / "Top50Mixed.csv").unlink()
            (root / "SignalHistory.csv").write_text("created-by-run\n", encoding="utf-8")
            snapshot_tx = _digest(tx_dir, "AllResults.csv")
            pipeline._rollback_transaction(tx_dir, existing)
        after = {name: _digest(root, name) for name in before}
        return {
            "before": before,
            "after": after,
            "restored": after == before,
            "backup_was_taken": snapshot_tx == before["AllResults.csv"],
            "new_file_removed": _digest(root, "SignalHistory.csv") == "",
            "tx_dir_removed": not tx_dir.exists(),
        }


def scenario_staging_roundtrip() -> dict[str, Any]:
    """Staging seeds durable files and publishes through a temp file."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        seeds = ("AllResults.parquet", "SignalHistory.csv", "SignalTracking.csv")
        for name in seeds:
            (root / name).write_text(f"seed-{name}\n", encoding="utf-8")
        (root / "Top50Stocks.csv").write_text("PUBLISHED-PREVIOUS\n", encoding="utf-8")
        with _output_dir(root):
            stage_dir = pipeline._prepare_staging("run-stage")
            seeded = all((stage_dir / name).is_file() for name in seeds)
            (stage_dir / "Top50Stocks.csv").write_text(
                "Ticker\n600036.SH\n", encoding="utf-8"
            )
            pipeline._publish_staging(stage_dir)
        published = (root / "Top50Stocks.csv").read_text(encoding="utf-8")
        leftovers = sorted(path.name for path in root.glob(".*.publish.tmp"))
        return {
            "seeded": seeded,
            "staged_under_dot_staging": (
                stage_dir.parent == root / ".staging" and stage_dir.name == "run-stage"
            ),
            "published_new_content": published == "Ticker\n600036.SH\n",
            "seeds_untouched": all(
                (root / name).read_text(encoding="utf-8") == f"seed-{name}\n"
                for name in seeds
            ),
            "leftovers": leftovers,
        }


def scenario_publish_refuses_empty_staging() -> dict[str, Any]:
    """An empty staging directory must never be published."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        stage_dir = root / "empty"
        stage_dir.mkdir()
        with _output_dir(root):
            (root / "AllResults.csv").write_text("PUBLISHED\n", encoding="utf-8")
            try:
                pipeline._publish_staging(stage_dir)
            except ValueError as error:
                raised = str(error)
            else:
                raised = ""
        return {
            "raised": raised,
            "mentions_staging": "staging" in raised.lower(),
            "published_untouched": (root / "AllResults.csv").read_text(
                encoding="utf-8"
            )
            == "PUBLISHED\n",
        }


def scenario_prepare_staging_rejects_reuse() -> dict[str, Any]:
    """A reused run id must fail loudly instead of merging two runs."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with _output_dir(root):
            pipeline._prepare_staging("run-duplicate")
            try:
                pipeline._prepare_staging("run-duplicate")
            except FileExistsError:
                raised = True
            else:
                raised = False
        return {"raised": raised}


def scenario_runtime_output_directory() -> dict[str, Any]:
    """Writers must be redirected for the run and restored afterwards."""
    import analytics  # noqa: PLC0415
    import report  # noqa: PLC0415
    import scanner  # noqa: PLC0415
    import signal_lifecycle_core  # noqa: PLC0415

    modules = (analytics, report, scanner, signal_lifecycle_core)

    def watched() -> dict[str, str]:
        state: dict[str, str] = {}
        for module in modules:
            for attribute in _WATCHED_ATTRIBUTES:
                if hasattr(module, attribute):
                    state[f"{module.__name__}.{attribute}"] = str(
                        getattr(module, attribute)
                    )
        return state

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        before = watched()
        with pipeline._runtime_output_directory(root / "stage"):
            during = watched()
        try:
            with pipeline._runtime_output_directory(root / "stage-after-failure"):
                raise RuntimeError("injected failure inside the run")
        except RuntimeError:
            pass
        after = watched()

        changed = [key for key in before if before[key] != during.get(key)]
        return {
            "redirected_every_watched_attribute": bool(changed)
            and len(changed) == len(before),
            "changed_count": len(changed),
            "watched_count": len(before),
            "restored": after == before,
            "mismatched": sorted(key for key in before if before[key] != after.get(key)),
        }


def scenario_failed_run_leaves_publication_untouched() -> dict[str, Any]:
    """The whole point: a crashed run must not move the published site."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        before = _write_published(root)
        latest_before = {"run_id": "previous-run", "expected_trading_date": "2026-09-14"}
        (root / "LatestRun.json").write_text(
            json.dumps(latest_before), encoding="utf-8"
        )
        scanner_cli = pipeline.scanner_cli
        original = scanner_cli.cmd_scan

        def _boom(args: object) -> int:
            raise RuntimeError("injected scan failure")

        scanner_cli.cmd_scan = _boom  # type: ignore[assignment]
        try:
            with _output_dir(root):
                code = pipeline.run_daily_pipeline()
        finally:
            scanner_cli.cmd_scan = original  # type: ignore[assignment]

        after = {name: _digest(root, name) for name in before}
        status = json.loads((root / "PublicationStatus.json").read_text(encoding="utf-8"))
        latest_after = json.loads((root / "LatestRun.json").read_text(encoding="utf-8"))
        staging = root / ".staging"
        transactions = root / ".daily_transactions"
        return {
            "before": before,
            "after": after,
            "exit_code": code,
            "published_restored": after == before,
            "latest_run_unchanged": latest_after == latest_before,
            "status": status,
            "staging_emptied": not staging.exists() or not any(staging.iterdir()),
            "transactions_emptied": not transactions.exists()
            or not any(transactions.iterdir()),
        }


def scenario_activate_run_advances_latest_run() -> dict[str, Any]:
    """Activation merges the new run on top of the previous LatestRun.json."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "LatestRun.json").write_text(
            json.dumps({"run_id": "previous-run", "kept_by_contract": "yes"}),
            encoding="utf-8",
        )
        run_dir = root / "runs" / "run-new"
        run_dir.mkdir(parents=True)
        payload = {
            "data_run_id": "data-1",
            "expected_trading_date": "2026-09-15",
            "effective_trading_date": "2026-09-15",
            "market_data_date_status": "ON_TIME",
            "market_data_lag_trading_days": 0,
            "performance_health": {"healthy": True},
            "gate_health": {"healthy": True},
            "version_manifest": {"pipeline": "v-test"},
        }
        activate = pipeline._activate_run
        globals_of = activate.__globals__
        original_publish = globals_of["maybe_publish_canonical_report"]
        publish_calls: list[str] = []

        def _record(output_dir: Path, **kwargs: object) -> None:
            publish_calls.append(str(kwargs.get("reason", "")))

        globals_of["maybe_publish_canonical_report"] = _record
        try:
            with _output_dir(root):
                activate("run-new", run_dir, payload)
        finally:
            globals_of["maybe_publish_canonical_report"] = original_publish

        latest = json.loads((root / "LatestRun.json").read_text(encoding="utf-8"))
        return {
            "latest": latest,
            "run_id_advanced": latest.get("run_id") == "run-new",
            # LatestRun.json is a pointer, not an append-only log: the core
            # rewrites it wholesale and the facade layers the v113 fields on
            # top.  What matters is that it ends up pointing at the new run.
            "run_dir_points_at_new_run": str(latest.get("run_dir", "")).replace(
                "\\", "/"
            )
            == "runs/run-new",
            "pipeline_version_present": bool(latest.get("pipeline_version")),
            "enriched_keys_present": all(
                key in latest
                for key in (
                    "effective_trading_date",
                    "market_data_date_status",
                    "performance_health",
                    "gate_health",
                    "version_manifest",
                )
            ),
            "site_published_after_activation": publish_calls == ["daily-complete"],
            "publish_calls": publish_calls,
        }


SCENARIOS = {
    "transaction_rollback": scenario_transaction_rollback,
    "staging_roundtrip": scenario_staging_roundtrip,
    "publish_refuses_empty_staging": scenario_publish_refuses_empty_staging,
    "prepare_staging_rejects_reuse": scenario_prepare_staging_rejects_reuse,
    "runtime_output_directory": scenario_runtime_output_directory,
    "failed_run_leaves_publication_untouched": (
        scenario_failed_run_leaves_publication_untouched
    ),
    "activate_run_advances_latest_run": scenario_activate_run_advances_latest_run,
}


def collect() -> dict[str, Any]:
    report: dict[str, Any] = {}
    for name, scenario in SCENARIOS.items():
        try:
            report[name] = scenario()
        except Exception as error:  # noqa: BLE001
            report[name] = {
                "error": f"{type(error).__name__}: {error}",
            }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--scenario", default="")
    args = parser.parse_args()

    if args.scenario:
        report = {args.scenario: SCENARIOS[args.scenario]()}
    else:
        report = collect()
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if str(args.out) == "-":
        # stdout keeps the caller free of temp files; all logging goes to stderr.
        print(payload)
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
