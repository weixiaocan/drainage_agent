from __future__ import annotations

import unittest
from pathlib import Path

from analysis.io import load_flow
from analysis.stats import check_data


class ProjectDataSmokeTests(unittest.TestCase):
    def test_project_demo_data_is_checked(self) -> None:
        root = Path(__file__).resolve().parents[1]
        result = check_data(load_flow(root=root))
        self.assertGreater(len(result), 0)

    def test_project_demo_flow_uses_canonical_schema(self) -> None:
        root = Path(__file__).resolve().parents[1]
        flow = load_flow(root=root)
        self.assertGreater(len(flow), 0)
        self.assertEqual(
            list(flow.columns),
            ["timestamp", "device_id", "point_id", "flow_lps", "level_m", "velocity_mps"],
        )


if __name__ == "__main__":
    unittest.main()
