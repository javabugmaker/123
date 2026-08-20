from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import gui_core
import gui_process_v64 as process_guard


class GuiProcessIntegrityTests(unittest.TestCase):
    def test_posix_subprocess_starts_in_own_session(self) -> None:
        with patch.object(process_guard.os, "name", "posix"):
            self.assertEqual(
                process_guard._popen_group_kwargs(),
                {"start_new_session": True},
            )

    def test_windows_cancel_uses_taskkill_tree(self) -> None:
        process = Mock()
        process.pid = 4321
        process.poll.return_value = None
        completed = Mock(returncode=0)

        with patch.object(process_guard.os, "name", "nt"), patch.object(
            process_guard.subprocess, "run", return_value=completed
        ) as run:
            process_guard.terminate_process_tree(process)

        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(command, ["taskkill", "/PID", "4321", "/T", "/F"])
        process.terminate.assert_not_called()

    def test_windows_taskkill_failure_falls_back_to_direct_terminate(self) -> None:
        process = Mock()
        process.pid = 4321
        process.poll.return_value = None
        completed = Mock(returncode=1)

        with patch.object(process_guard.os, "name", "nt"), patch.object(
            process_guard.subprocess, "run", return_value=completed
        ):
            process_guard.terminate_process_tree(process)

        process.terminate.assert_called_once_with()

    def test_finished_process_is_not_terminated(self) -> None:
        process = Mock()
        process.poll.return_value = 0
        with patch.object(process_guard.subprocess, "run") as run:
            process_guard.terminate_process_tree(process)
        run.assert_not_called()
        process.terminate.assert_not_called()

    def test_install_patches_stable_gui_base_class(self) -> None:
        process_guard.install()
        self.assertIs(gui_core.ScannerGUI.run_process, process_guard.run_process)
        self.assertIs(gui_core.ScannerGUI._cancel_process, process_guard._cancel_process)

    def test_config_hook_installs_when_gui_core_is_loaded(self) -> None:
        import config

        process_guard._INSTALLED = False
        # Restore the legacy methods temporarily so the hook has observable work.
        legacy_run = Mock()
        legacy_cancel = Mock()
        with patch.object(gui_core.ScannerGUI, "run_process", legacy_run), patch.object(
            gui_core.ScannerGUI, "_cancel_process", legacy_cancel
        ):
            config._install_gui_runtime_contract_if_ready()
            self.assertIs(gui_core.ScannerGUI.run_process, process_guard.run_process)
            self.assertIs(gui_core.ScannerGUI._cancel_process, process_guard._cancel_process)
        process_guard._INSTALLED = True


if __name__ == "__main__":
    unittest.main()
