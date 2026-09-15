"""Daily publication safety contract for the GitHub Pages delivery path.

``daily_pipeline.py`` is the only code that runs for real in CI on every trading
day (``.github/workflows/daily-pages.yml``), and it is what decides whether the
published site advances or keeps serving yesterday's snapshot.  Before this
module existed that mechanism had no regression test at all: the local suite
could be fully green while the publication transaction was broken.

The assertions below lock the four properties the contract is built on:

* a run writes into ``output/.staging/<RunId>`` and never into the canonical set
  while it is in flight;
* a failed run restores the pre-run published set byte for byte;
* ``LatestRun.json`` advances only after publication succeeds;
* the site is published downstream of activation, never before.

The pipeline is exercised in a child process (``tests/daily_publication_probe.py``)
because ``import daily_pipeline`` transitively imports ``main``, which installs
overlays process-wide -- the same reason ``tests/assembly_manifest.py`` refuses
to capture entry points in-process.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE = Path(__file__).resolve().with_name("daily_publication_probe.py")
_PROBE_TIMEOUT_SECONDS = 300
_PROBE_ATTEMPTS = 4


def _run_probe() -> subprocess.CompletedProcess[str]:
    """Run the probe, retrying only transport-level ``OSError``.

    Some sandboxed Windows hosts fail ``subprocess.run(..., capture_output=True)``
    with ``OSError: [WinError 6] 句柄无效`` while setting up the pipes.  That is
    a transport failure, not a pipeline failure, so it is retried.  A non-zero
    exit from the probe itself is never retried -- it is reported as is.
    """
    last_error: OSError | None = None
    for _attempt in range(_PROBE_ATTEMPTS):
        try:
            return subprocess.run(
                [sys.executable, str(PROBE), "--out", "-"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT_SECONDS,
                check=False,
            )
        except OSError as error:  # pragma: no cover - environment dependent
            last_error = error
    raise AssertionError(
        f"could not launch the daily publication probe: {last_error}"
    ) from last_error


@pytest.fixture(scope="module")
def probe() -> dict[str, Any]:
    result = _run_probe()
    if result.returncode != 0:
        raise AssertionError(
            "daily publication probe failed\n"
            f"returncode={result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    try:
        return dict(json.loads(result.stdout))
    except json.JSONDecodeError as error:
        raise AssertionError(
            f"probe produced no parseable report: {error}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        ) from error


def _scenario(probe: dict[str, Any], name: str) -> dict[str, Any]:
    assert name in probe, f"probe did not report {name!r}: {sorted(probe)}"
    payload = probe[name]
    assert "error" not in payload, f"{name} raised: {payload['error']}"
    return dict(payload)


def test_probe_completed_every_scenario(probe: dict[str, Any]) -> None:
    expected = {
        "transaction_rollback",
        "staging_roundtrip",
        "publish_refuses_empty_staging",
        "prepare_staging_rejects_reuse",
        "runtime_output_directory",
        "failed_run_leaves_publication_untouched",
        "activate_run_advances_latest_run",
    }
    missing = sorted(expected - set(probe))
    assert not missing, f"probe is missing scenarios: {missing}"


def test_transaction_rollback_restores_the_published_set(probe: dict[str, Any]) -> None:
    scenario = _scenario(probe, "transaction_rollback")
    assert scenario["backup_was_taken"], "pre-run snapshot was not written to the tx dir"
    assert scenario["restored"], (
        "rollback did not restore the published set\n"
        f"before={scenario['before']}\nafter={scenario['after']}"
    )
    assert scenario["new_file_removed"], (
        "a file created by the failed run survived the rollback"
    )
    assert scenario["tx_dir_removed"], "transaction directory was left behind"


def test_staging_seeds_durable_state_and_publishes_atomically(
    probe: dict[str, Any],
) -> None:
    scenario = _scenario(probe, "staging_roundtrip")
    assert scenario["staged_under_dot_staging"], (
        "staging must live under output/.staging/<RunId>"
    )
    assert scenario["seeded"], "durable state was not seeded into staging"
    assert scenario["seeds_untouched"], "publishing mutated the canonical seed files"
    assert scenario["published_new_content"], "staged output did not reach output/"
    assert scenario["leftovers"] == [], (
        f"atomic publish left temp files behind: {scenario['leftovers']}"
    )


def test_publish_refuses_an_empty_staging_directory(probe: dict[str, Any]) -> None:
    scenario = _scenario(probe, "publish_refuses_empty_staging")
    assert scenario["raised"], "publishing an empty staging directory was allowed"
    assert scenario["mentions_staging"], f"unhelpful error: {scenario['raised']!r}"
    assert scenario["published_untouched"], (
        "a refused publish still touched the canonical output"
    )


def test_prepare_staging_rejects_a_reused_run_id(probe: dict[str, Any]) -> None:
    scenario = _scenario(probe, "prepare_staging_rejects_reuse")
    assert scenario["raised"], (
        "reusing a run id was accepted; two runs could merge into one staging dir"
    )


def test_runtime_writers_are_redirected_and_restored(probe: dict[str, Any]) -> None:
    scenario = _scenario(probe, "runtime_output_directory")
    assert scenario["watched_count"] > 0, "no writer attribute was under observation"
    assert scenario["redirected_every_watched_attribute"], (
        "some writers kept pointing at the canonical output during the run: "
        f"{scenario['changed_count']}/{scenario['watched_count']} redirected"
    )
    assert scenario["restored"], (
        "writer attributes were not restored after the run (and after a failure): "
        f"{scenario['mismatched']}"
    )


def test_a_failed_run_leaves_the_published_site_untouched(
    probe: dict[str, Any],
) -> None:
    scenario = _scenario(probe, "failed_run_leaves_publication_untouched")
    assert scenario["exit_code"] == 2, (
        f"a failed daily run must exit 2, got {scenario['exit_code']}"
    )
    assert scenario["published_restored"], (
        "a failed run changed the published set\n"
        f"before={scenario['before']}\nafter={scenario['after']}"
    )
    assert scenario["latest_run_unchanged"], (
        "LatestRun.json advanced even though the run failed"
    )
    assert scenario["status"]["status"] == "failed", scenario["status"]
    assert scenario["status"]["latest_run_unchanged"] is True, scenario["status"]
    assert scenario["staging_emptied"], "staging residue survived the failed run"
    assert scenario["transactions_emptied"], "transaction residue survived the rollback"


def test_activation_advances_latest_run_then_publishes_the_site(
    probe: dict[str, Any],
) -> None:
    scenario = _scenario(probe, "activate_run_advances_latest_run")
    assert scenario["run_id_advanced"], scenario["latest"]
    assert scenario["run_dir_points_at_new_run"], (
        f"LatestRun.json points at {scenario['latest'].get('run_dir')!r}"
    )
    assert scenario["pipeline_version_present"], "provenance version was not recorded"
    assert scenario["enriched_keys_present"], (
        f"v113 observability fields missing from {sorted(scenario['latest'])}"
    )
    assert scenario["site_published_after_activation"], (
        f"site publication was not triggered by activation: {scenario['publish_calls']}"
    )
