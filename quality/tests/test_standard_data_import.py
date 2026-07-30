from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")
TestClient = fastapi_testclient.TestClient

from quality.tests.test_web_app import FakeAgent, make_deps
from analysis.io import StandardDataStore
from web.app import create_app


def _client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            tmp_path,
            deps_factory=make_deps,
            agent_factory=lambda _deps: FakeAgent(),
        )
    )


def test_standard_flow_template_downloads_and_imports_without_questions(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    template = client.get("/api/standard-flow-template")

    assert template.status_code == 200
    assert "attachment" in template.headers["content-disposition"]
    assert template.content.startswith(b"\xef\xbb\xbf")

    project = client.post("/api/projects", json={"name": "模板项目"}).json()
    batch = client.post(
        f"/api/projects/{project['id']}/batches",
        json={"name": "模板批次"},
    ).json()
    response = client.post(
        f"/api/projects/{project['id']}/batches/{batch['id']}/imports",
        files={"file": ("filled_template.csv", template.content, "text/csv")},
    )

    assert response.status_code == 201
    inspection = response.json()
    assert inspection["status"] == "ready"
    assert inspection["anomalies"] == []
    assert all(column["field"] for column in inspection["columns"])


def test_web_uploads_demo_flow_as_immutable_batch_input_and_inspects_it(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "北区监测"}).json()
    batch = client.post(
        f"/api/projects/{project['id']}/batches",
        json={"name": "三月流量"},
    ).json()
    raw = (
        "数据时间,1分钟内记录总数,设备编号,流量(L/s)(均值),流速(m/s)(均值),液位(m)(均值)\n"
        "2026-03-07 00:00:00,1,17620135,37.759,0.032,2.664\n"
    ).encode("utf-8")

    response = client.post(
        f"/api/projects/{project['id']}/batches/{batch['id']}/imports",
        files={"file": ("35891_W1.csv", raw, "text/csv")},
    )

    assert response.status_code == 201
    inspection = response.json()
    assert inspection["status"] == "ready"
    assert inspection["encoding"] == "utf-8"
    assert inspection["row_count"] == 1
    assert inspection["columns"] == [
        {"source": "数据时间", "field": "timestamp", "type": "datetime", "unit": None},
        {"source": "设备编号", "field": "device_id", "type": "string", "unit": None},
        {"source": "流量(L/s)(均值)", "field": "flow_lps", "type": "number", "unit": "L/s"},
        {"source": "流速(m/s)(均值)", "field": "velocity_mps", "type": "number", "unit": "m/s"},
        {"source": "液位(m)(均值)", "field": "level_m", "type": "number", "unit": "m"},
    ]
    assert inspection["anomalies"] == []

    downloaded = client.get(
        f"/api/projects/{project['id']}/batches/{batch['id']}"
        f"/imports/{inspection['id']}/raw"
    )
    assert downloaded.status_code == 200
    assert downloaded.content == raw


def test_common_columns_with_missing_units_wait_for_engineer_confirmation(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "南区监测"}).json()
    batch = client.post(
        f"/api/projects/{project['id']}/batches",
        json={"name": "常见英文列"},
    ).json()

    response = client.post(
        f"/api/projects/{project['id']}/batches/{batch['id']}/imports",
        files={
            "file": (
                "W2.csv",
                b"time,site,flow,level,velocity\n2026-03-07 00:00,W2,3.6,1200,0.4\n",
                "text/csv",
            )
        },
    )

    assert response.status_code == 201
    inspection = response.json()
    assert inspection["status"] == "pending_confirmation"
    assert inspection["columns"] == [
        {"source": "time", "field": "timestamp", "type": "datetime", "unit": None},
        {"source": "site", "field": "point_id", "type": "string", "unit": None},
        {"source": "flow", "field": "flow_lps", "type": "number", "unit": None},
        {"source": "level", "field": "level_m", "type": "number", "unit": None},
        {"source": "velocity", "field": "velocity_mps", "type": "number", "unit": None},
    ]
    assert inspection["anomalies"] == [
        "字段 flow 的单位缺失，请确认源单位",
        "字段 level 的单位缺失，请确认源单位",
        "字段 velocity 的单位缺失，请确认源单位",
    ]
    standard = client.get(
        f"/api/projects/{project['id']}/batches/{batch['id']}/standard/flow"
    )
    assert standard.status_code == 409
    assert standard.json()["detail"] == "标准数据尚未确认生成"


def test_engineer_confirms_units_and_gets_canonical_standard_preview(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "东区监测"}).json()
    batch = client.post(
        f"/api/projects/{project['id']}/batches",
        json={"name": "单位换算"},
    ).json()
    uploaded = client.post(
        f"/api/projects/{project['id']}/batches/{batch['id']}/imports",
        files={
            "file": (
                "W3.csv",
                b"time,site,flow,level,velocity\n2026-03-07 00:00,W3,3.6,1200,0.4\n",
                "text/csv",
            )
        },
    ).json()

    confirmed = client.put(
        f"/api/projects/{project['id']}/batches/{batch['id']}"
        f"/imports/{uploaded['id']}/mapping",
        json={
            "mapping": {
                "time": "timestamp",
                "site": "point_id",
                "flow": "flow_lps",
                "level": "level_m",
                "velocity": "velocity_mps",
            },
            "units": {"flow": "m3/h", "level": "mm", "velocity": "m/s"},
        },
    )

    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    standard = client.get(
        f"/api/projects/{project['id']}/batches/{batch['id']}/standard/flow"
    )
    assert standard.status_code == 200
    assert standard.json() == {
        "columns": [
            "timestamp",
            "device_id",
            "point_id",
            "flow_lps",
            "level_m",
            "velocity_mps",
        ],
        "units": {
            "flow_lps": "L/s",
            "level_m": "m",
            "velocity_mps": "m/s",
        },
        "rows": [
            {
                "timestamp": "2026-03-07T00:00:00",
                "device_id": None,
                "point_id": "W3",
                "flow_lps": 1.0,
                "level_m": 1.2,
                "velocity_mps": 0.4,
            }
        ],
    }


def test_empty_csv_returns_actionable_error_without_creating_an_import(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "空数据项目"}).json()
    batch = client.post(
        f"/api/projects/{project['id']}/batches",
        json={"name": "空数据批次"},
    ).json()

    response = client.post(
        f"/api/projects/{project['id']}/batches/{batch['id']}/imports",
        files={"file": ("empty.csv", b"time,site,flow\n", "text/csv")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "文件不包含数据行，请上传至少一行监测数据"


def test_invalid_time_returns_actionable_confirmation_error(tmp_path: Path) -> None:
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "时间校验"}).json()
    batch = client.post(
        f"/api/projects/{project['id']}/batches",
        json={"name": "错误时间"},
    ).json()
    uploaded = client.post(
        f"/api/projects/{project['id']}/batches/{batch['id']}/imports",
        files={
            "file": (
                "W4.csv",
                b"time,site,flow\nnot-a-time,W4,1\n",
                "text/csv",
            )
        },
    ).json()

    response = client.put(
        f"/api/projects/{project['id']}/batches/{batch['id']}"
        f"/imports/{uploaded['id']}/mapping",
        json={
            "mapping": {
                "time": "timestamp",
                "site": "point_id",
                "flow": "flow_lps",
            },
            "units": {"flow": "L/s"},
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "时间字段包含无效值，请修正后重新确认"


def test_index_exposes_batch_standard_data_import_workflow(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert 'id="dataImportForm"' in response.text
    assert 'id="batchImportForm"' not in response.text
    assert 'id="batchImportFile" name="files" type="file" accept=".csv" multiple required' in response.text
    assert 'id="flowFileNames"' in response.text
    assert 'id="filterRevisionFileName"' in response.text
    assert 'id="filterRevisionFileButtonLabel"' in response.text
    assert '"当前已确认：筛选结果.xlsx"' in response.text
    assert '"上传修改版"' in response.text
    assert "重新上传将替换当前筛选结果" in response.text
    assert "并清空已有分析结果、报告和运行记录" in response.text
    assert "state.filter.filter_id" in response.text
    assert "当前项目结果文件" not in response.text
    assert 'id="refreshResults"' not in response.text
    assert 'id="downloadProjectResults"' in response.text
    assert 'id="downloadProjectAll"' in response.text
    assert "/downloads/results" in response.text
    assert "/downloads/all" in response.text
    assert 'className = "message-artifacts"' in response.text
    assert "data.artifacts || []" in response.text
    assert "data.derived_state_reset" in response.text
    assert "已选择 ${names.length} 个文件" in response.text
    assert 'names.join("、")' not in response.text
    assert "重新识别完成：已替换当前标准数据" not in response.text
    assert "选择监测 CSV" in response.text
    assert "尚未上传" in response.text
    assert "/workspace/state" in response.text
    assert "/workspace/reset" in response.text
    assert "归档旧对话" in response.text
    assert 'name="rainfall_file"' in response.text
    assert 'name="site_info_file"' in response.text
    assert "上传并智能识别全部列名" in response.text
    assert 'id="importQuestions"' in response.text
    assert 'id="importInspection"' in response.text
    assert 'id="standardPreview"' not in response.text
    assert "编码" in response.text
    assert "原始列名" in response.text
    assert "类型" in response.text
    assert "源单位" in response.text
    assert "/imports" in response.text
    assert "/mapping" in response.text
    assert "/api/standard-flow-template" in response.text
    assert 'name="files"' in response.text
    assert "multiple" in response.text
    assert "保存映射配置" not in response.text
    assert "确认全部匹配并生成标准数据" in response.text
    assert "/batch-imports" in response.text
    assert "/auxiliary/inspect" in response.text
    assert "/auxiliary/confirm" in response.text
    assert 'id="importMappingDialog"' in response.text
    assert 'id="auxiliaryMappingDialog"' in response.text
    assert 'id="continueImportMapping"' in response.text
    assert "表头相同的 CSV 已自动归组" in response.text
    assert "importGroups()" in response.text
    assert 'id="closeImportMappingDialog"' in response.text
    assert 'aria-label="关闭监测数据列名匹配弹窗"' in response.text
    assert 'id="importDialogStatus"' in response.text


def test_multiple_monitoring_files_are_confirmed_as_one_standard_dataset(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "批量导入"}).json()
    batch = client.post(
        f"/api/projects/{project['id']}/batches",
        json={"name": "两个点位"},
    ).json()
    endpoint = (
        f"/api/projects/{project['id']}/batches/{batch['id']}/batch-imports"
    )
    response = client.post(
        endpoint,
        files=[
            ("files", ("W1.csv", b"time,flow\n2026-01-01 00:00:00,1\n", "text/csv")),
            ("files", ("W2.csv", b"time,flow\n2026-01-01 00:00:00,2\n", "text/csv")),
        ],
    )
    assert response.status_code == 201
    imports = response.json()["imports"]
    confirm = client.put(
        endpoint + "/mapping",
        json={
            "imports": [
                {
                    "import_id": item["id"],
                    "mapping": {"time": "timestamp", "flow": "flow_lps"},
                    "units": {"flow": "L/s"},
                }
                for item in imports
            ]
        },
    )
    assert confirm.status_code == 200
    assert confirm.json()["import_count"] == 2
    assert confirm.json()["row_count"] == 2
    preview = client.get(
        f"/api/projects/{project['id']}/batches/{batch['id']}/standard/flow"
    ).json()
    assert [row["point_id"] for row in preview["rows"]] == ["W1", "W2"]
    replaced = client.put(
        endpoint + "/mapping",
        json={
            "imports": [
                {
                    "import_id": item["id"],
                    "mapping": {"time": "timestamp", "flow": "flow_lps"},
                    "units": {"flow": "m3/h"},
                }
                for item in imports
            ]
        },
    )
    assert replaced.status_code == 200
    assert replaced.json()["replaced_existing"] is True
    replaced_preview = client.get(
        f"/api/projects/{project['id']}/batches/{batch['id']}/standard/flow"
    ).json()
    assert replaced_preview["rows"][0]["flow_lps"] == pytest.approx(1 / 3.6)


def test_auxiliary_data_is_inspected_then_saved_to_current_batch(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "辅助数据"}).json()
    batch = client.post(
        f"/api/projects/{project['id']}/batches",
        json={"name": "列名确认"},
    ).json()
    endpoint = (
        f"/api/projects/{project['id']}/batches/{batch['id']}/auxiliary"
    )
    rainfall = b"date,rain\n2026-01-01,2.5\n"
    inspected = client.post(
        endpoint + "/inspect",
        files={"rainfall_file": ("rain.csv", rainfall, "text/csv")},
    )
    assert inspected.status_code == 200
    assert inspected.json()["rainfall"]["row_count"] == 1
    confirmed = client.post(
        endpoint + "/confirm",
        files={"rainfall_file": ("rain.csv", rainfall, "text/csv")},
        data={"mappings": json.dumps(
            {"rainfall": {"date": "timestamp", "rain": "rain_mm"}}
        )},
    )
    assert confirmed.status_code == 200
    standard = (
        tmp_path
        / "var"
        / "projects"
        / project["id"]
        / "batches"
        / batch["id"]
        / "standard"
        / "rainfall.csv"
    )
    assert standard.read_text(encoding="utf-8").splitlines() == [
        "timestamp,rain_mm",
        "2026-01-01,2.5",
    ]


def test_site_information_matches_first_report_table_contract(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "点位表头"}).json()
    batch = client.post(
        f"/api/projects/{project['id']}/batches",
        json={"name": "列名确认"},
    ).json()
    endpoint = (
        f"/api/projects/{project['id']}/batches/{batch['id']}/auxiliary"
    )
    content = BytesIO()
    pd.DataFrame(
        [
            {
                "安装监测点位": "W1",
                "类型": "流量计",
                "形状": "圆管",
                "管径(m)": 1.5,
                "井深(m)": 5.4,
                "设备安装时间": "2026-03-01",
                "管道类型": "污水管",
            }
        ]
    ).to_excel(content, index=False)
    payload = content.getvalue()

    inspected = client.post(
        endpoint + "/inspect",
        files={
            "site_info_file": (
                "sites.xlsx",
                payload,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert inspected.status_code == 200
    columns = {
        item["source"]: item["field"]
        for item in inspected.json()["sites"]["columns"]
    }
    assert columns == {
        "安装监测点位": "point_id",
        "类型": "device_type",
        "形状": "shape",
        "管径(m)": "diameter_m",
        "井深(m)": "well_depth_m",
        "设备安装时间": "install_time",
        "管道类型": "pipe_type",
    }

    confirmed = client.post(
        endpoint + "/confirm",
        files={
            "site_info_file": (
                "sites.xlsx",
                payload,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={"mappings": json.dumps({"sites": columns})},
    )

    assert confirmed.status_code == 200
    saved = pd.read_csv(
        tmp_path
        / "var"
        / "projects"
        / project["id"]
        / "batches"
        / batch["id"]
        / "standard"
        / "sites.csv"
    )
    assert list(saved.columns) == [
        "point_id",
        "device_type",
        "shape",
        "diameter_m",
        "well_depth_m",
        "install_time",
        "pipe_type",
    ]
    assert saved.iloc[0].to_dict() == {
        "point_id": "W1",
        "device_type": "流量计",
        "shape": "圆管",
        "diameter_m": 1.5,
        "well_depth_m": 5.4,
        "install_time": "2026-03-01",
        "pipe_type": "污水管",
    }


def test_analysis_reads_confirmed_batch_data_only_through_standard_contract(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "标准读取"}).json()
    batch = client.post(
        f"/api/projects/{project['id']}/batches",
        json={"name": "分析输入"},
    ).json()
    uploaded = client.post(
        f"/api/projects/{project['id']}/batches/{batch['id']}/imports",
        files={
            "file": (
                "W5.csv",
                b"time,site,flow\n2026-03-07 00:00,W5,2.5\n",
                "text/csv",
            )
        },
    ).json()
    client.put(
        f"/api/projects/{project['id']}/batches/{batch['id']}"
        f"/imports/{uploaded['id']}/mapping",
        json={
            "mapping": {
                "time": "timestamp",
                "site": "point_id",
                "flow": "flow_lps",
            },
            "units": {"flow": "L/s"},
        },
    )

    flow = StandardDataStore(tmp_path / "var" / "projects").load_flow(
        project["id"],
        batch["id"],
    )

    assert list(flow.columns) == [
        "timestamp",
        "device_id",
        "point_id",
        "flow_lps",
        "level_m",
        "velocity_mps",
    ]
    assert flow.iloc[0].to_dict() == {
        "timestamp": flow.iloc[0]["timestamp"],
        "device_id": None,
        "point_id": "W5",
        "flow_lps": 2.5,
        "level_m": None,
        "velocity_mps": None,
    }
    assert flow.iloc[0]["timestamp"].isoformat() == "2026-03-07T00:00:00"


def test_existing_demo_filename_deterministically_supplies_point_id(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "演示数据"}).json()
    batch = client.post(
        f"/api/projects/{project['id']}/batches",
        json={"name": "W1 演示"},
    ).json()
    uploaded = client.post(
        f"/api/projects/{project['id']}/batches/{batch['id']}/imports",
        files={
            "file": (
                "35891_W1.csv",
                (
                    "数据时间,1分钟内记录总数,设备编号,流量(L/s)(均值),"
                    "流速(m/s)(均值),液位(m)(均值)\n"
                    "2026-03-07 00:00:00,1,17620135,37.759,0.032,2.664\n"
                ).encode(),
                "text/csv",
            )
        },
    ).json()

    response = client.put(
        f"/api/projects/{project['id']}/batches/{batch['id']}"
        f"/imports/{uploaded['id']}/mapping",
        json={
            "mapping": {
                "数据时间": "timestamp",
                "设备编号": "device_id",
                "流量(L/s)(均值)": "flow_lps",
                "流速(m/s)(均值)": "velocity_mps",
                "液位(m)(均值)": "level_m",
            },
            "units": {
                "流量(L/s)(均值)": "L/s",
                "流速(m/s)(均值)": "m/s",
                "液位(m)(均值)": "m",
            },
        },
    )

    assert response.status_code == 200
    preview = client.get(
        f"/api/projects/{project['id']}/batches/{batch['id']}/standard/flow"
    ).json()
    assert preview["rows"][0]["point_id"] == "W1"
    assert preview["rows"][0]["device_id"] == "17620135"


def test_conflicting_header_unit_waits_for_engineer_confirmation(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "冲突单位"}).json()
    batch = client.post(
        f"/api/projects/{project['id']}/batches",
        json={"name": "单位冲突"},
    ).json()

    response = client.post(
        f"/api/projects/{project['id']}/batches/{batch['id']}/imports",
        files={
            "file": (
                "W6.csv",
                b"timestamp,point_id,flow_lps(m3/h)\n2026-03-07,W6,3.6\n",
                "text/csv",
            )
        },
    )

    assert response.status_code == 201
    inspection = response.json()
    assert inspection["status"] == "pending_confirmation"
    assert inspection["columns"][-1] == {
        "source": "flow_lps(m3/h)",
        "field": "flow_lps",
        "type": "number",
        "unit": None,
    }
    assert inspection["anomalies"] == [
        "字段 flow_lps(m3/h) 的名称表示 L/s，但表头单位为 m3/h，请确认源单位"
    ]


def test_unknown_columns_are_visible_and_missing_required_mapping_is_actionable(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "陌生格式"}).json()
    batch = client.post(
        f"/api/projects/{project['id']}/batches",
        json={"name": "待人工映射"},
    ).json()
    uploaded_response = client.post(
        f"/api/projects/{project['id']}/batches/{batch['id']}/imports",
        files={
            "file": (
                "vendor.csv",
                b"when,station,value\n2026-03-07,P1,2.5\n",
                "text/csv",
            )
        },
    )

    assert uploaded_response.status_code == 201
    uploaded = uploaded_response.json()
    assert uploaded["status"] == "pending_confirmation"
    assert uploaded["columns"] == [
        {"source": "when", "field": None, "type": "string", "unit": None},
        {"source": "station", "field": None, "type": "string", "unit": None},
        {"source": "value", "field": None, "type": "number", "unit": None},
    ]
    assert uploaded["anomalies"] == [
        "无法自动映射必需字段: flow_lps, point_id, timestamp；请修正字段映射"
    ]

    confirmed = client.put(
        f"/api/projects/{project['id']}/batches/{batch['id']}"
        f"/imports/{uploaded['id']}/mapping",
        json={"mapping": {"when": "timestamp"}, "units": {}},
    )
    assert confirmed.status_code == 400
    assert confirmed.json()["detail"] == "缺少必需字段映射: flow_lps, point_id"


def test_imports_cannot_be_read_across_projects_or_batches(tmp_path: Path) -> None:
    client = _client(tmp_path)
    north = client.post("/api/projects", json={"name": "北区"}).json()
    south = client.post("/api/projects", json={"name": "南区"}).json()
    north_batch = client.post(
        f"/api/projects/{north['id']}/batches",
        json={"name": "北区批次"},
    ).json()
    south_batch = client.post(
        f"/api/projects/{south['id']}/batches",
        json={"name": "南区批次"},
    ).json()
    uploaded = client.post(
        f"/api/projects/{north['id']}/batches/{north_batch['id']}/imports",
        files={
            "file": (
                "W7.csv",
                b"time,site,flow\n2026-03-07,W7,1\n",
                "text/csv",
            )
        },
    ).json()

    cross_project = client.get(
        f"/api/projects/{south['id']}/batches/{south_batch['id']}"
        f"/imports/{uploaded['id']}/raw"
    )
    wrong_batch = client.get(
        f"/api/projects/{north['id']}/batches/{south_batch['id']}"
        f"/imports/{uploaded['id']}/raw"
    )

    assert cross_project.status_code == 404
    assert wrong_batch.status_code == 404


def test_confirmed_standard_data_can_be_replaced_without_changing_original_upload(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "重新识别数据"}).json()
    batch = client.post(
        f"/api/projects/{project['id']}/batches",
        json={"name": "允许替换"},
    ).json()
    raw = b"time,site,flow\n2026-03-07,W8,1\n"
    uploaded = client.post(
        f"/api/projects/{project['id']}/batches/{batch['id']}/imports",
        files={"file": ("W8.csv", raw, "text/csv")},
    ).json()
    endpoint = (
        f"/api/projects/{project['id']}/batches/{batch['id']}"
        f"/imports/{uploaded['id']}/mapping"
    )
    mapping = {
        "mapping": {
            "time": "timestamp",
            "site": "point_id",
            "flow": "flow_lps",
        },
        "units": {"flow": "L/s"},
    }

    assert client.put(endpoint, json=mapping).status_code == 200
    repeated = client.put(endpoint, json={**mapping, "units": {"flow": "m3/h"}})
    raw_download = client.get(
        f"/api/projects/{project['id']}/batches/{batch['id']}"
        f"/imports/{uploaded['id']}/raw"
    )
    preview = client.get(
        f"/api/projects/{project['id']}/batches/{batch['id']}/standard/flow"
    ).json()

    assert repeated.status_code == 200
    assert repeated.json()["replaced_existing"] is True
    assert raw_download.content == raw
    assert preview["rows"][0]["flow_lps"] == pytest.approx(1 / 3.6)
