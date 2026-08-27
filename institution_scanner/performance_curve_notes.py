"""Internal semantic notes for the model-health curve feature.

This module intentionally contains no runtime code.  It exists to keep the
semantic distinction explicit for future maintainers: ResearchCohortNAV is a
longitudinal diagnostic proxy built from realised horizon outcomes, not an
execution-accounting NAV.  Do not label it as a broker-replicable portfolio
until a daily position ledger is introduced.
"""
