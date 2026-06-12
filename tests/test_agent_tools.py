from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from agent.deps import AgentDeps, AgentSettings, Paths, SessionState, ensure_directories
from agent.tools.module_tools import check_data_impl, query_stats_impl
from agent.tools.python_tool import run_python_impl


def make_deps(root: Path) -> AgentDeps:
    paths = Paths.from_root(root)
    ensure_directories(paths)
    return AgentDeps(
        paths=paths,
        settings=AgentSettings(model="test", base_url=None, api_key=None),
        logger=logging.getLogger("test"),
        session=SessionState(),
        project_notes="",
    )


def write_flow(deps: AgentDeps) -> None:
    pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=5, freq="min"),
            "flow": [1, 2, 3, 4, 5],
            "level": [0.1, 0.2, 0.3, 0.4, 0.5],
            "velocity": [0.2, 0.2, 0.3, 0.3, 0.4],
        }
    ).to_csv(deps.paths.flow_dir / "100_W1.csv", index=False)
    deps.paths.rainfall_file.write_text("timestamp,rain\n2026-01-01,0\n", encoding="utf-8")


class AgentToolTests(unittest.TestCase):
    def test_check_data_reads_flow_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            deps = make_deps(Path(tmp))
            write_flow(deps)
            result = check_data_impl(deps)
            self.assertEqual(result["status"], "ok")
            self.assertIn("数据体检完成", result["summary"])

    def test_query_stats_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            deps = make_deps(Path(tmp))
            write_flow(deps)
            result = query_stats_impl(deps, dry_only=False)
            self.assertEqual(result["status"], "ok")
            self.assertTrue(deps.paths.combined_xlsx.exists())

    def test_run_python_success_and_workspace_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            deps = make_deps(Path(tmp))
            write_flow(deps)
            result = run_python_impl(deps, "(WORKSPACE_DIR / 'out.txt').write_text(str(len(load_flow(clean=False))), encoding='utf-8')")
            self.assertEqual(result["status"], "ok", result)
            self.assertEqual((deps.paths.workspace / "out.txt").read_text(encoding="utf-8"), "5")


if __name__ == "__main__":
    unittest.main()
