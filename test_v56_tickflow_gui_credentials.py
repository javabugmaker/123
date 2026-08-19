from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import downloader
import tickflow_settings


class _FakeTickFlow:
    free_calls = 0
    api_keys: list[str | None] = []
    instances: list["_FakeTickFlow"] = []

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key
        self.closed = False
        type(self).api_keys.append(api_key)
        type(self).instances.append(self)

    @classmethod
    def free(cls):
        cls.free_calls += 1
        return cls(api_key=None)

    def close(self) -> None:
        self.closed = True


class V56TickFlowCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeTickFlow.free_calls = 0
        _FakeTickFlow.api_keys = []
        _FakeTickFlow.instances = []
        downloader._TICKFLOW_CLIENT = None
        downloader._TICKFLOW_CLIENT_MODE = None
        downloader._TICKFLOW_CLIENT_CREDENTIAL_ID = None

    def tearDown(self) -> None:
        downloader._TICKFLOW_CLIENT = None
        downloader._TICKFLOW_CLIENT_MODE = None
        downloader._TICKFLOW_CLIENT_CREDENTIAL_ID = None

    def test_angle_brackets_are_removed_from_copied_key(self) -> None:
        self.assertEqual(
            tickflow_settings.normalize_api_key("<tk_unit_test>"), "tk_unit_test"
        )

    def test_gui_local_key_overrides_stale_windows_environment_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env.local"
            path.write_text(
                "TICKFLOW_API_MODE=authenticated\n"
                "TICKFLOW_API_KEY=tk_gui_local\n",
                encoding="utf-8",
            )
            with patch.object(
                tickflow_settings, "DEFAULT_SETTINGS_PATH", path
            ), patch.dict(
                os.environ, {"TICKFLOW_API_KEY": "tk_stale_windows"}, clear=False
            ), patch.object(
                downloader, "TickFlow", _FakeTickFlow
            ):
                client = downloader._tickflow()
                label = downloader.get_data_source_label()

        self.assertEqual(client.api_key, "tk_gui_local")
        self.assertEqual(_FakeTickFlow.api_keys, ["tk_gui_local"])
        self.assertEqual(_FakeTickFlow.free_calls, 0)
        self.assertEqual(label, "TickFlow API")
        self.assertEqual(downloader._TICKFLOW_CLIENT_MODE, "authenticated")

    def test_explicit_free_mode_beats_existing_environment_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env.local"
            path.write_text("TICKFLOW_API_MODE=free\n", encoding="utf-8")
            with patch.object(
                tickflow_settings, "DEFAULT_SETTINGS_PATH", path
            ), patch.dict(
                os.environ, {"TICKFLOW_API_KEY": "tk_windows_key"}, clear=False
            ), patch.object(
                downloader, "TickFlow", _FakeTickFlow
            ):
                client = downloader._tickflow()
                label = downloader.get_data_source_label()

        self.assertIsNone(client.api_key)
        self.assertEqual(_FakeTickFlow.free_calls, 1)
        self.assertEqual(label, "TickFlow Free")
        self.assertEqual(downloader._TICKFLOW_CLIENT_MODE, "free")

    def test_changing_gui_key_recreates_authenticated_client(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env.local"
            path.write_text(
                "TICKFLOW_API_MODE=authenticated\nTICKFLOW_API_KEY=tk_key_one\n",
                encoding="utf-8",
            )
            with patch.object(
                tickflow_settings, "DEFAULT_SETTINGS_PATH", path
            ), patch.dict(os.environ, {}, clear=True), patch.object(
                downloader, "TickFlow", _FakeTickFlow
            ):
                first = downloader._tickflow()
                path.write_text(
                    "TICKFLOW_API_MODE=authenticated\nTICKFLOW_API_KEY=tk_key_two\n",
                    encoding="utf-8",
                )
                second = downloader._tickflow()

        self.assertIsNot(first, second)
        self.assertTrue(first.closed)
        self.assertEqual(first.api_key, "tk_key_one")
        self.assertEqual(second.api_key, "tk_key_two")
        self.assertEqual(_FakeTickFlow.api_keys, ["tk_key_one", "tk_key_two"])

    def test_save_and_free_switch_persist_only_to_requested_local_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env.local"
            environment = dict(os.environ)
            try:
                tickflow_settings.save_tickflow_api_key("<tk_saved_local>", path)
                saved = path.read_text(encoding="utf-8")
                self.assertIn("TICKFLOW_API_MODE=authenticated", saved)
                self.assertIn("TICKFLOW_API_KEY=tk_saved_local", saved)
                self.assertNotIn("<tk_saved_local>", saved)

                tickflow_settings.use_tickflow_free(path)
                free = path.read_text(encoding="utf-8")
                self.assertIn("TICKFLOW_API_MODE=free", free)
                self.assertNotIn("TICKFLOW_API_KEY=", free)
            finally:
                os.environ.clear()
                os.environ.update(environment)


if __name__ == "__main__":
    unittest.main()
