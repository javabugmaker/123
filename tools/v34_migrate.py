from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    if old not in text:
        raise RuntimeError(f"expected block not found in {path}: {old[:80]!r}")
    write(path, text.replace(old, new, 1))


def move_module_docstring_before_future(path: str) -> None:
    text = read(path)
    match = re.match(
        r'from __future__ import annotations\n\n(?P<doc>""".*?""")\n\n',
        text,
        flags=re.DOTALL,
    )
    if match is None:
        return
    rest = text[match.end():]
    write(path, f'{match.group("doc")}\n\nfrom __future__ import annotations\n\n{rest}')


for module in (
    "classification.py",
    "daily_pipeline.py",
    "gui.py",
    "model_calibration.py",
    "performance_cache.py",
    "scan_service.py",
):
    move_module_docstring_before_future(module)

replace_once(
    "score.py",
    "from dataclasses import dataclass, field\nfrom typing import Any\n",
    "from dataclasses import dataclass, field\nfrom pathlib import Path\nfrom typing import Any\n",
)

replace_once(
    "gui.py",
    "        self._nav_buttons: dict[str, object] = {}\n",
    "        self._nav_buttons: dict[str, ctk.CTkButton] = {}\n",
)
replace_once(
    "gui.py",
    "    total = max(0, int(round(float(seconds or 0.0))))\n",
    "    total = max(0, round(float(seconds or 0.0)))\n",
)
replace_once(
    "gui.py",
    "    def _build_ui_configure_styles(self) -> None:\n        tk = _core.tk\n        ttk = _core.ttk\n",
    "    def _build_ui_configure_styles(self) -> None:\n        ttk = _core.ttk\n",
)

replace_once(
    "report.py",
    '                "RankingPenaltyReason": r.ranking_penalty_reason,\n'
    '                "DecisionState": r.decision_state,\n'
    '                "DecisionReason": r.decision_reason,\n'
    '                "TradeReadiness": r.trade_readiness or r.ranking_eligibility,\n',
    '                "RankingPenaltyReason": r.ranking_penalty_reason,\n'
    '                "TradeReadiness": r.trade_readiness or r.ranking_eligibility,\n',
)

replace_once(
    "analytics.py",
    '    frame["BacktestCacheHit"] = frame.get(\n'
    '        "BacktestCacheHit", pd.Series(False, index=frame.index)\n'
    '    ).fillna(False).astype(bool)\n',
    '    frame["BacktestCacheHit"] = frame.get(\n'
    '        "BacktestCacheHit", pd.Series(False, index=frame.index)\n'
    '    ).eq(True)\n',
)
replace_once(
    "analytics.py",
    '    target = max(2, int(round(cpu_limit * utilization)))\n',
    '    target = max(2, round(cpu_limit * utilization))\n',
)

replace_once(
    "gui_core.py",
    '        total, active, confirmed, breakout, actionable, average = self._market_overview_values(rows, indexes)\n',
    '        total, _active, _confirmed, breakout, actionable, average = self._market_overview_values(rows, indexes)\n',
)
replace_once(
    "gui_core.py",
    '        cache_first = getattr(self, "cache_first", None)\n'
    '        if cache_first is not None and cache_first.get() and not self.force_download.get():\n'
    '            command.append("--cache-first")\n'
    '        refresh_fundamentals = getattr(self, "refresh_fundamentals", None)\n'
    '        if refresh_fundamentals is not None and refresh_fundamentals.get():\n'
    '            command.append("--refresh-fundamentals")\n',
    '        if self.cache_first.get() and not self.force_download.get():\n'
    '            command.append("--cache-first")\n'
    '        if self.refresh_fundamentals.get():\n'
    '            command.append("--refresh-fundamentals")\n',
)
replace_once(
    "gui_core.py",
    '        scan_cancel_event = getattr(self, "_scan_cancel_event", None)\n'
    '        if scan_cancel_event is not None:\n'
    '            scan_cancel_event.set()\n',
    '        if self._scan_cancel_event is not None:\n'
    '            self._scan_cancel_event.set()\n',
)

replace_once(
    "fundamental_quality.py",
    '    passed = [name for name, value in factors.items() if value is True]\n',
    "",
)

config_text = read("config.py")
if "import logging\nimport sys\nimport time\n" not in config_text[:500]:
    config_text = config_text.replace(
        "from __future__ import annotations\n\n",
        "from __future__ import annotations\n\nimport logging\nimport sys\nimport time\n",
        1,
    )
config_text = config_text.replace(
    "# 集中日志配置\n# ======================================================================\nimport logging\nimport sys\nimport time\nfrom typing import Literal\n",
    "# 集中日志配置\n# ======================================================================\n",
    1,
)
config_text = config_text.replace(
    'PIPELINE_VERSION: str = "2026-08-10-v33-mixed-diversity-nan"',
    'PIPELINE_VERSION: str = "2026-08-10-v34-static-quality"',
    1,
)
write("config.py", config_text)

write(
    "pyproject.toml",
    '''[tool.ruff]\ntarget-version = "py311"\nline-length = 120\nextend-exclude = [\n  ".venv",\n  "cache",\n  "output",\n  "logs",\n  ".ruff_cache",\n  "tools/apply_project_hardening.py",\n  "tools/v34_migrate.py",\n]\n\n[tool.ruff.lint]\nselect = ["F", "I", "UP035", "RUF046", "RUF059"]\n''',
)

# Pandas stubs produce a large number of false-positive attribute diagnostics
# when reportAttributeAccessIssue is forced globally. Keep the existing broad
# project compatibility profile, and enforce strict attribute access on the GUI
# modules where it catches real widget/state typing regressions.
pyright = json.loads(read("pyrightconfig.json"))
pyright["reportAttributeAccessIssue"] = "none"
write("pyrightconfig.json", json.dumps(pyright, ensure_ascii=False, indent=2) + "\n")
write(
    "pyrightconfig.gui.json",
    json.dumps(
        {
            "extends": "./pyrightconfig.json",
            "include": ["gui.py", "gui_core.py"],
            "reportAttributeAccessIssue": "error",
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
)

write(
    ".github/workflows/static-quality.yml",
    '''name: Static Quality\n\non:\n  pull_request:\n    branches: [main]\n  push:\n    branches: [main]\n\njobs:\n  static-quality:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n      - uses: actions/setup-python@v5\n        with:\n          python-version: '3.11'\n      - name: Install project and static tools\n        run: |\n          pip install -r requirements.txt\n          pip install ruff pyright\n      - name: Ruff\n        run: ruff check .\n      - name: Pyright project\n        run: pyright\n      - name: Pyright GUI strict\n        run: pyright -p pyrightconfig.gui.json\n      - name: Compile\n        run: python -m compileall -q .\n''',
)

print("v34 migration applied")
