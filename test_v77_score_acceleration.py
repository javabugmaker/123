from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

import score_acceleration_v77 as accelerated
import score_acceleration_v79 as latest
import score_core
import score_runtime_v97 as runtime
import score_scale_migration_v95 as scale


def _legacy_available(
    df: pd.DataFrame,
    columns: tuple[str, ...],
    minimum: int = 1,
) -> bool:
    if not all(column in df.columns for column in columns):
        return False
    values = df[list(columns)].apply(pd.to_numeric, errors="coerce")
    values = values.replace([np.inf, -np.inf], np.nan)
    return len(values.dropna()) >= minimum and not values.iloc[-1].isna().any()


def _legacy_volume_score(df: pd.DataFrame) -> float:
    if len(df) < 120:
        return 0.0
    score = 0.0
    if "VolMA20" in df.columns and "VolMA120" in df.columns:
        vol_ma20 = df["VolMA20"].replace([np.inf, -np.inf], np.nan)
        vol_ma120 = df["VolMA120"].replace([np.inf, -np.inf], np.nan)
        ratio_series = (vol_ma20 / vol_ma120.replace(0, np.nan)).dropna()
        if len(ratio_series) >= score_core.VOLUME_ACCUM_MIN_DAYS:
            consecutive = 0
            for value in ratio_series.iloc[::-1]:
                if value >= score_core.VOLUME_ACCUM_RATIO:
                    consecutive += 1
                else:
                    break
            if consecutive >= score_core.VOLUME_ACCUM_MIN_DAYS:
                score += 4.0 + score_core._clamp(
                    (consecutive - score_core.VOLUME_ACCUM_MIN_DAYS) / 80.0
                ) * 6.0
            ratio_now = float(ratio_series.iloc[-1])
            score += score_core._clamp(
                (ratio_now - score_core.VOLUME_ACCUM_RATIO) / 0.8
            ) * 3.0
            if len(ratio_series) >= 20:
                ratio_change = float(ratio_series.iloc[-1] - ratio_series.iloc[-20])
                score += score_core._clamp(ratio_change / 0.5) * 4.0
    if "VolZScore" in df.columns:
        z_recent = (
            df["VolZScore"].replace([np.inf, -np.inf], np.nan).dropna().iloc[-30:]
        )
        if len(z_recent) >= 10:
            z_now = float(z_recent.iloc[-1])
            positive_days = float((z_recent > 0).mean())
            score += positive_days * 3.0
            score += score_core._clamp(z_now / 2.0) * 2.0
    return score_core._clamp(score, 0.0, 25.0)


class ScoreAccelerationTests(unittest.TestCase):
    def test_finite_value_check_matches_dataframe_contract(self) -> None:
        rng = np.random.default_rng(77)
        frame = pd.DataFrame(
            {
                "A": rng.normal(size=300),
                "B": rng.normal(size=300),
            }
        )
        frame.loc[[10, 20, 30], "A"] = np.nan
        frame.loc[[40, 50], "B"] = np.inf
        for minimum in (1, 60, 250, 296):
            self.assertEqual(
                accelerated._has_finite_values_fast(
                    frame, ("A", "B"), minimum=minimum
                ),
                _legacy_available(frame, ("A", "B"), minimum=minimum),
            )

    def test_volume_score_matches_legacy(self) -> None:
        rng = np.random.default_rng(2026)
        frame = pd.DataFrame(
            {
                "VolMA20": rng.uniform(1.0, 3.0, 400),
                "VolMA120": rng.uniform(1.0, 2.5, 400),
                "VolZScore": rng.normal(size=400),
            }
        )
        frame.loc[35, "VolZScore"] = np.nan
        frame.loc[70, "VolMA120"] = 0.0
        self.assertAlmostEqual(
            accelerated.score_volume(frame),
            _legacy_volume_score(frame),
            places=12,
        )

    def test_v77_kernel_contract_survives_canonical_runtime_composition(self) -> None:
        # Historical accelerators remain independently equivalent implementation
        # kernels. The public endpoint, however, belongs to the current v95+
        # mathematical contract rather than whichever installer ran last.
        accelerated.install()
        latest.install()
        runtime.install()
        self.assertIs(
            score_core._score_dimensions_available,
            latest._score_dimensions_available,
        )
        self.assertIs(score_core.score_volume, scale.score_volume)
        self.assertIs(scale._ORIGINAL_SCORE_VOLUME, latest.score_volume)
        self.assertEqual(
            score_core.SCORE_RUNTIME_COMPOSITION_VERSION,
            runtime.SCORE_RUNTIME_COMPOSITION_VERSION,
        )


if __name__ == "__main__":
    unittest.main()
