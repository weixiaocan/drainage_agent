from __future__ import annotations

import unittest
from pathlib import Path

from agent.deps import build_deps
from agent.tools.module_tools import check_data_impl, query_stats_impl


class ProjectDataSmokeTests(unittest.TestCase):
    def test_project_demo_data_is_checked(self) -> None:
        deps = build_deps(Path(__file__).resolve().parents[1])
        result = check_data_impl(deps)
        self.assertEqual(result["status"], "ok")
        self.assertGreater(len(result["data"]["table"]), 0)

    def test_query_stats_on_project_demo_data(self) -> None:
        deps = build_deps(Path(__file__).resolve().parents[1])
        result = query_stats_impl(deps, dry_only=False)
        self.assertEqual(result["status"], "ok")
        self.assertIn("查询统计完成", result["summary"])
        self.assertTrue(deps.paths.manifest.exists())


if __name__ == "__main__":
    unittest.main()
