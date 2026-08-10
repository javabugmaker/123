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


# Correct module metadata ordering where earlier refactors put __future__ before
# the module docstring. This is semantic-neutral and makes editor import sorting
# deterministic.
for module in (
    "classification.py",
    "daily_pipeline.py",
    "gui.py",
    "model_calibration.py",
    "performance_cache.py",
    "scan_service.py",
):
    move_module_docstring_before_future(module)

# Real Pyright defect: Path is used in the model-weight cache signature.
replace_once(
    "score.py",
    "from dataclasses import dataclass, field\nfrom typing import Any\n",
    "from dataclasses import dataclass, field\nfrom pathlib import Path\nfrom typing import Any\n",
)

# Fix the two Pylance errors shown in VS Code by giving navigation widgets their
# actual type instead of object. Also remove a redundant integer conversion and
# one dead local.
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

# The same DecisionState/DecisionReason values were emitted twice in one dict.
# Python silently kept the latter; remove the duplicate pair so static analysis
# can protect this export schema going forward without changing CSV semantics.
replace_once(
    "report.py",
    '                "RankingPenaltyReason": r.ranking_penalty_reason,\n'
    '                "DecisionState": r.decision_state,\n'
    '                "DecisionReason": r.decision_reason,\n'
    '                "TradeReadiness": r.trade_readiness or r.ranking_eligibility,\n',
    '                "RankingPenaltyReason": r.ranking_penalty_reason,\n'
    '                "TradeReadiness": r.trade_readiness or r.ranking_eligibility,\n',
)

# Avoid pandas object-dtype fillna downcasting behavior that is deprecated in
# pandas 2.3 and changes in pandas 3. Equality produces the intended boolean
# mask without relying on implicit downcasting.
replace_once(
    "analytics.py",
    '    frame["BacktestCacheHit"] = frame.get(\n'
    '        "BacktestCacheHit", pd.Series(False, index=frame.index)\n'
    '    ).fillna(False).astype(bool)\n',
    '    frame["BacktestCacheHit"] = frame.get(\n'
    '        "BacktestCacheHit", pd.Series(False, index=frame.index)\n'
    '    ).eq(True)\n',
)

# Remove a dead intermediate that was left behind when quality-gate reporting
# switched to failed/unknown explanations.
replace_once(
    "fundamental_quality.py",
    '    passed = [name for name, value in factors.items() if value is True]\n',
    "",
)

# Consolidate logging imports at module scope; Literal was unused.
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

# Ruff policy intentionally targets correctness/import hygiene. Chinese UI text
# legitimately uses full-width punctuation, and this project contains long
# explanatory strings, so RUF001/RUF002 and E501 are deliberately not selected.
write(
    "pyproject.toml",
    '''[tool.ruff]\ntarget-version = "py311"\nline-length = 120\nextend-exclude = [\n  ".venv",\n  "cache",\n  "output",\n  "logs",\n  ".ruff_cache",\n  "tools/apply_project_hardening.py",\n  "tools/v34_migrate.py",\n]\n\n[tool.ruff.lint]\nselect = ["F", "I", "UP035", "RUF046", "RUF059"]\n''',
)

pyright = json.loads(read("pyrightconfig.json"))
pyright["reportAttributeAccessIssue"] = "error"
write("pyrightconfig.json", json.dumps(pyright, ensure_ascii=False, indent=2) + "\n")

write(
    ".github/workflows/static-quality.yml",
    '''name: Static Quality\n\non:\n  pull_request:\n    branches: [main]\n  push:\n    branches: [main]\n\njobs:\n  static-quality:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n      - uses: actions/setup-python@v5\n        with:\n          python-version: '3.11'\n      - name: Install project and static tools\n        run: |\n          pip install -r requirements.txt\n          pip install ruff pyright\n      - name: Ruff\n        run: ruff check .\n      - name: Pyright\n        run: pyright\n      - name: Compile\n        run: python -m compileall -q .\n''',
)

print("v34 migration applied")
