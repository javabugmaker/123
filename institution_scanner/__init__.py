"""Canonical forward-path package for InstitutionScanner.

Legacy root modules remain compatibility entry points. New reliability, contract
and research-terminal work belongs here so future releases do not add another
layer of versioned monkey patches.
"""

from .contracts import CHALLENGER_CONTRACT, PRODUCTION_CONTRACT

__all__ = ["PRODUCTION_CONTRACT", "CHALLENGER_CONTRACT"]
