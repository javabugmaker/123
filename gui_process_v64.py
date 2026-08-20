"""v64 GUI subprocess lifecycle hardening.

Backtests may create a ProcessPoolExecutor beneath the CLI subprocess.  Killing
only the direct ``python main.py backtest`` process can leave worker Python
processes alive on Windows.  This facade patches the stable GUI base class so
all compatibility subprocesses start in their own process group/session and a
user cancellation terminates the whole tree.

The in-process scan cancellation path remains cooperative and unchanged.
"""

from __future__ import annotations

import os
import signal
import subprocess
from typing import Any

import gui_core as _core

_INSTALLED = False


def _popen_group_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        flag = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        return {"creationflags": flag} if flag else {}
    return {"start_new_session": True}


def terminate_process_tree(process: subprocess.Popen[str] | None) -> None:
    """Best-effort termination of the CLI process and its multiprocessing tree."""
    if process is None:
        return
    try:
        return_code = process.poll()
    except (OSError, subprocess.SubprocessError):
        return_code = None
    # A real Popen.poll() returns None while running and an integer after exit.
    # Treat unexpected compatibility objects conservatively as still running so
    # cancellation can fall back to their direct terminate() method.
    if isinstance(return_code, int):
        return
    try:
        pid = int(process.pid)
    except (TypeError, ValueError, AttributeError):
        try:
            process.terminate()
        except OSError:
            pass
        return
    if os.name == "nt":
        # taskkill /T walks the child process tree; /F is required because
        # ProcessPoolExecutor workers do not share a console control handler.
        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=10,
            )
            if completed.returncode == 0:
                return
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
            return
        except (OSError, ProcessLookupError):
            pass
    try:
        process.terminate()
    except OSError:
        pass


def run_process(self, command: list[str]) -> None:
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        self.process = subprocess.Popen(
            command,
            cwd=_core.PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            **_popen_group_kwargs(),
        )
        if self._cancel_requested:
            terminate_process_tree(self.process)
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self._log_queue.put(line)
        code = self.process.wait()
        self.process = None
        self._scan_completion_queue.put(("finished", code))
    except (OSError, subprocess.SubprocessError) as exc:
        self._scan_completion_queue.put(("failed", str(exc)))


def _cancel_process(self) -> None:
    self._cancel_requested = True
    if hasattr(self, "cancel_button"):
        self.cancel_button.configure(state=_core.tk.DISABLED)
    self.status.set("正在取消任务")
    scan_cancel_event = getattr(self, "_scan_cancel_event", None)
    if scan_cancel_event is not None:
        scan_cancel_event.set()
    try:
        terminate_process_tree(self.process)
    except OSError as exc:
        self.append_log(f"取消任务失败：{exc}\n")


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _core.ScannerGUI.run_process = run_process
    _core.ScannerGUI._cancel_process = _cancel_process
    _core.terminate_process_tree = terminate_process_tree
    _core._popen_group_kwargs = _popen_group_kwargs
    _INSTALLED = True


install()
