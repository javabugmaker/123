"""Compatibility facade for the canonical fundamental-data service.

TickFlow remains the only market-data and universe provider.  The implementation
for low-frequency financial reports lives under :mod:`institution_scanner`;
this module preserves the long-standing import path used by CLI and GUI code.
"""

from institution_scanner.fundamental_schema import FUNDAMENTAL_REQUIRED_COLUMNS
from institution_scanner.fundamentals import (
    _CACHE_PATH,
    _META_PATH,
    _REPORT_CACHE_PATH,
    FUNDAMENTAL_COLUMNS,
    FUNDAMENTAL_PROVIDER_VERSION,
    FUNDAMENTAL_SCHEMA_VERSION,
    REPORT_COLUMNS,
    FundamentalRefreshCancelled,
    fundamental_data_path,
    refresh_fundamental_data,
)

__all__ = [
    "FUNDAMENTAL_COLUMNS",
    "FUNDAMENTAL_PROVIDER_VERSION",
    "FUNDAMENTAL_REQUIRED_COLUMNS",
    "FUNDAMENTAL_SCHEMA_VERSION",
    "FundamentalRefreshCancelled",
    "REPORT_COLUMNS",
    "_CACHE_PATH",
    "_META_PATH",
    "_REPORT_CACHE_PATH",
    "fundamental_data_path",
    "refresh_fundamental_data",
]
