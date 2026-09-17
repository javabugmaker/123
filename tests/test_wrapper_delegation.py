"""Wrapper/legacy pairs must keep delegating to what they wrap.

Two of the largest "duplicated" symbols in the tree -- ``cmd_backtest``
(``backtest_command_v76`` vs ``main_core``) and ``_ticker_backtest_rows``
(``analytics`` facade vs ``analytics_core``) -- look like competing
implementations in a same-name scan.  They are not:

* ``backtest_command_v76.py:39`` keeps ``_LEGACY_CMD_BACKTEST = _main.cmd_backtest``
  and ``:119`` calls it, wrapping it in transactional publication.
* ``analytics.py:58`` keeps ``_LEGACY_TICKER_BACKTEST_ROWS``, ``:226`` calls it
  first thing, and ``:430`` rebinds the wrapper onto the core.

So a parity test would be meaningless here -- the wrapper's output is the
legacy output *plus* an enhancement, and comparing them for equality tests
nothing.  What can actually break is the delegation itself: if someone rewrites
a wrapper and drops the call, the enhancement silently becomes a replacement
and the legacy behaviour disappears with no test noticing.

These assertions are static on purpose.  ``analytics`` ends with
``sys.modules[__name__] = _core``, so importing it yields the core module and
the facade source cannot be observed in-process.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _module_functions(name: str) -> dict[str, ast.FunctionDef]:
    tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
    return {
        n.name: n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _called_names(fn: ast.FunctionDef) -> set[str]:
    return {
        n.func.id
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }


def test_cmd_backtest_wrapper_delegates_to_the_legacy_command() -> None:
    fn = _module_functions("backtest_command_v76.py")["cmd_backtest"]
    assert "_LEGACY_CMD_BACKTEST" in _called_names(fn), (
        "cmd_backtest no longer calls _LEGACY_CMD_BACKTEST; the v76 wrapper "
        "would stop being a wrapper and silently drop the legacy command"
    )


def test_ticker_backtest_rows_wrapper_delegates_to_the_legacy_rows() -> None:
    fn = _module_functions("analytics.py")["_ticker_backtest_rows"]
    assert "_LEGACY_TICKER_BACKTEST_ROWS" in _called_names(fn), (
        "_ticker_backtest_rows no longer calls _LEGACY_TICKER_BACKTEST_ROWS; "
        "the facade wrapper would replace the core implementation instead of "
        "adding resonance on top of it"
    )


def test_both_wrappers_are_rebound_onto_the_module_they_extend() -> None:
    """A wrapper that is never installed is dead code with a convincing name."""
    facade = (ROOT / "analytics.py").read_text(encoding="utf-8")
    assert "_core._ticker_backtest_rows = _ticker_backtest_rows" in facade

    command = (ROOT / "backtest_command_v76.py").read_text(encoding="utf-8")
    assert "_main.cmd_backtest = cmd_backtest" in command
