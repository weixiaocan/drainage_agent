from __future__ import annotations

import unittest
from pathlib import Path

from agent.deps import build_deps
from agent.tools.inspect_tools import describe_data_impl
from agent.tools.module_tools import run_data_stats_impl


class ProjectDataSmokeTests(unittest.TestCase):
    def test_project_demo_data_is_described(self) -> None:
        deps = build_deps(Path(__file__).resolve().parents[1])
        result = describe_data_impl(deps)
        self.assertEqual(result["status"], "ok")
        self.assertGreater(result["data"]["flow_file_count"], 0)

    def test_run_data_stats_on_project_demo_data(self) -> None:
        deps = build_deps(Path(__file__).resolve().parents[1])
        result = run_data_stats_impl(deps)
        self.assertEqual(result["status"], "ok")
        self.assertIn("数据收集率统计完成", result["summary"])
        self.assertTrue(deps.paths.combined_xlsx.exists())
        self.assertTrue(deps.paths.manifest.exists())


if __name__ == "__main__":
    unittest.main()

