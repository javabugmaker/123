from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import scan_service
import scanner
import scanner_resume_v59 as resume_v59
import scanner_resume_v68 as resume_v68


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [10.0],
            "High": [10.5],
            "Low": [9.5],
            "Close": [10.2],
            "Volume": [1_000_000.0],
            "Amount": [10_200_000.0],
        },
        index=pd.to_datetime(["2026-08-19"]),
    )


class ResumePublicationIntegrityTests(unittest.TestCase):
    def tearDown(self) -> None:
        if hasattr(scanner, "_defer_checkpoint_clear_until_publish"):
            scanner._defer_checkpoint_clear_until_publish = False

    def test_v68_remains_visible_through_v59_compatibility_module(self) -> None:
        self.assertIs(scanner.run_scan, resume_v68.run_scan)
        self.assertIs(resume_v59.run_scan, resume_v68.run_scan)
        self.assertIs(scan_service.run_scan, resume_v68.run_scan)

    def test_downloaded_frame_is_pinned_into_enrichment(self) -> None:
        frame = _frame()
        captured: dict[str, object] = {}
        sentinel = object()

        def fake_enrich(results, source, frames=None):
            captured["frames"] = frames
            captured["source"] = source

        def fake_base(**kwargs):
            scanner.download_batch([], source="tickflow")
            scanner.enrich_results(
                [SimpleNamespace(ticker="000001.SZ")],
                "tickflow",
                frames={},
            )
            scanner.clear_checkpoint()
            return sentinel

        with patch.object(
            scanner, "download_batch", return_value={"000001.SZ": frame}
        ), patch.object(scanner, "enrich_results", side_effect=fake_enrich), patch.object(
            scanner, "clear_checkpoint"
        ) as clear, patch.object(resume_v68, "_BASE_RUN_SCAN", side_effect=fake_base):
            scanner._defer_checkpoint_clear_until_publish = True
            result = resume_v68.run_scan(resume=True, data_source="tickflow")

        self.assertIs(result, sentinel)
        pinned = captured["frames"]
        self.assertIsInstance(pinned, dict)
        self.assertIs(pinned["000001.SZ"], frame)
        self.assertEqual(captured["source"], "tickflow")
        clear.assert_not_called()

    def test_forced_scan_still_clears_old_checkpoint_before_deferring_final_clear(self) -> None:
        sentinel = object()

        def fake_base(**kwargs):
            scanner.clear_checkpoint()  # mandatory initial clear
            scanner.clear_checkpoint()  # successful-enrichment clear
            return sentinel

        with patch.object(scanner, "clear_checkpoint") as clear, patch.object(
            resume_v68, "_BASE_RUN_SCAN", side_effect=fake_base
        ):
            scanner._defer_checkpoint_clear_until_publish = True
            result = resume_v68.run_scan(
                force_download=True,
                resume=True,
                data_source="tickflow",
            )

        self.assertIs(result, sentinel)
        clear.assert_called_once_with()

    def test_scan_service_clears_checkpoint_after_successful_canonical_publication(self) -> None:
        sentinel = object()
        request = scan_service.ScanRequest()
        with patch.object(
            scan_service, "_legacy_execute_scan", return_value=sentinel
        ) as legacy, patch.object(scanner, "clear_checkpoint") as clear:
            result = scan_service.execute_scan(request)

        self.assertIs(result, sentinel)
        legacy.assert_called_once()
        clear.assert_called_once_with()
        self.assertFalse(
            bool(getattr(scanner, "_defer_checkpoint_clear_until_publish", False))
        )

    def test_scan_service_keeps_checkpoint_when_publication_fails(self) -> None:
        request = scan_service.ScanRequest()
        with patch.object(
            scan_service,
            "_legacy_execute_scan",
            side_effect=OSError("simulated publish failure"),
        ), patch.object(scanner, "clear_checkpoint") as clear:
            with self.assertRaisesRegex(OSError, "simulated publish failure"):
                scan_service.execute_scan(request)

        clear.assert_not_called()
        self.assertFalse(
            bool(getattr(scanner, "_defer_checkpoint_clear_until_publish", False))
        )


if __name__ == "__main__":
    unittest.main()
