from __future__ import annotations

import unittest

import config


class V40VersionBoundaryTests(unittest.TestCase):
    def test_output_release_keeps_v39_scoring_cache_boundary(self):
        self.assertIn("v40", config.PIPELINE_VERSION)
        self.assertIn("v40", config.DECISION_INTEGRITY_VERSION)
        self.assertIn("v40", config.OUTPUT_CONTRACT_VERSION)
        self.assertIn("v39", config.SCORING_VERSION)
        self.assertNotIn("v40", config.SCORING_VERSION)
        self.assertIn("v38", config.FUNDAMENTAL_GATE_VERSION)


if __name__ == "__main__":
    unittest.main()
