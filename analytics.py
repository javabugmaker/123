"""v51 analytics facade with process-safe benchmark time-basis alignment.

The historical analytics implementation lives in ``analytics_core``.  Installing
execution alignment at this public import boundary guarantees that CLI callers,
GUI/daily-pipeline entrypoints, tests and library users all observe the same
T+1-open stock/benchmark return basis.
"""

from __future__ import annotations

import sys

import analytics_core as _core
from analytics_core import *  # noqa: F403
from backtest_alignment import install_analytics_alignment

install_analytics_alignment(_core)

sys.modules[__name__] = _core
