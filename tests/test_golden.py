from __future__ import annotations

from pathlib import Path

import pandas as pd
from pandas.testing import assert_frame_equal

from analysis.stats import query_stats


def test_query_stats_matches_golden_sample() -> None:
    flow = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=6, freq="min"),
            "point_id": ["W1", "W1", "W1", "W2", "W2", "W2"],
            "flow_lps": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            "level_m": [0.1] * 6,
            "velocity_mps": [0.2] * 6,
        }
    )
    actual = query_stats(flow, metrics=["flow_lps"], aggs=["mean", "max", "min"])
    expected = pd.read_csv(Path(__file__).resolve().parent / "golden" / "query_stats_sample.csv")
    assert_frame_equal(actual, expected, check_dtype=False, rtol=1e-9)
