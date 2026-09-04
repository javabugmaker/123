"""Stable package entry point for the existing command-line interface."""

from __future__ import annotations

import importlib


def main() -> int:
    """Delegate to the compatibility CLI while package extraction continues."""
    module = importlib.import_module("main")
    legacy_main = module.main
    return int(legacy_main())
