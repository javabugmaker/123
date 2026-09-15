"""Shared pytest configuration.

Only one concern lives here: keeping an environment without optional GUI
support from derailing the entire suite.

``test_fundamental_progress.py`` imports ``gui_core``, which imports ``tkinter``
at module scope. A collection error aborts the whole run, so on a Python build
without tkinter the whole suite becomes unrunnable rather than just that one
module. Ignoring the module at collection time keeps the remaining tests green
and makes the reason explicit instead of cryptic.

Import-mode and ``sys.path`` concerns are deliberately NOT handled here; they
belong to ``pythonpath`` under ``[tool.pytest.ini_options]`` in
``pyproject.toml`` so that ``pytest`` and ``python -m pytest`` behave
identically.
"""

from __future__ import annotations

import importlib.util

collect_ignore: list[str] = []

if importlib.util.find_spec("tkinter") is None:
    collect_ignore.append("test_fundamental_progress.py")
