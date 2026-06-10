from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from agent.deps import AgentDeps, AgentSettings, Paths, SessionState, ensure_directories
from agent.tools.inspect_tools import describe_data_impl, list_results_impl
from agent.tools.memory_tool import record_note_impl
from agent.tools.module_tools import run_dry_analysis_impl
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


class AgentToolTests(unittest.TestCase):
    def test_describe_data_reports_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            deps = make_deps(Path(tmp))
            result = describe_data_impl(deps)
            self.assertEqual(result["status"], "error")
            self.assertIn("未找到 flow", result["summary"])

    def test_describe_data_reads_flow_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            deps = make_deps(Path(tmp))
            deps.paths.flow_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                {
                    "timestamp": pd.date_range("2026-01-01", periods=5, freq="min"),
                    "flow": [1, 2, 3, 4, 5],
                }
            ).to_csv(deps.paths.flow_dir / "100_W1.csv", index=False)
            deps.paths.rainfall_file.write_text("timestamp,rain\n2026-01-01,0\n", encoding="utf-8")
            deps.paths.site_info_file.write_bytes(b"placeholder")
            result = describe_data_impl(deps)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["data"]["flow_file_count"], 1)

    def test_list_results_reads_excel_sheets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            deps = make_deps(Path(tmp))
            with pd.ExcelWriter(deps.paths.combined_xlsx) as writer:
                pd.DataFrame({"a": [1]}).to_excel(writer, sheet_name="旱天分析", index=False)
            result = list_results_impl(deps)
            self.assertEqual(result["status"], "ok")
            rel = "outputs/综合分析结果.xlsx"
            self.assertIn(rel, result["data"]["results"])
            self.assertIn("旱天分析", result["data"]["results"][rel]["sheets"])

    def test_run_dry_analysis_blocks_without_filter_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            deps = make_deps(Path(tmp))
            result = run_dry_analysis_impl(deps)
            self.assertEqual(result["status"], "blocked")
            self.assertIn("run_data_filter", result["hint"])

    def test_run_python_success_and_workspace_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            deps = make_deps(Path(tmp))
            result = run_python_impl(deps, "print(DATA_DIR.name)\n(WORKSPACE_DIR / 'out.txt').write_text('ok', encoding='utf-8')")
            self.assertEqual(result["status"], "ok")
            self.assertTrue((deps.paths.workspace / "out.txt").exists())

    def test_run_python_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            deps = make_deps(Path(tmp))
            result = run_python_impl(deps, "raise RuntimeError('boom')")
            self.assertEqual(result["status"], "error")
            self.assertIn("boom", result["data"]["stderr"])

    def test_record_note_appends_project_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            deps = make_deps(Path(tmp))
            result = record_note_impl(deps, "#5 点位曲线平坦属正常")
            self.assertEqual(result["status"], "ok")
            self.assertIn("#5 点位", deps.paths.notes.read_text(encoding="utf-8"))

    def test_all_module_runners_import(self) -> None:
        from pipeline.modules.data_filter.runner import run as _data_filter
        from pipeline.modules.data_stats.runner import run as _data_stats
        from pipeline.modules.dry_analysis.runner import run as _dry
        from pipeline.modules.event_stats.runner import run as _event
        from pipeline.modules.pattern_analysis.runner import run as _pattern
        from pipeline.modules.rainfall_analysis.runner import run as _rain
        from pipeline.modules.rdii_analysis.runner import run as _rdii
        from pipeline.modules.report_assembler.runner import run as _report
        from pipeline.modules.risk_analysis.runner import run as _risk

        self.assertTrue(all([_data_filter, _data_stats, _dry, _event, _pattern, _rain, _rdii, _report, _risk]))


if __name__ == "__main__":
    unittest.main()

