from __future__ import annotations

from pathlib import Path

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")
TestClient = fastapi_testclient.TestClient

from quality.tests.test_web_app import FakeAgent, make_deps
from web.app import create_app


def _client(tmp_path: Path, **kwargs: object) -> TestClient:
    return TestClient(
        create_app(
            tmp_path,
            deps_factory=make_deps,
            agent_factory=lambda _deps: FakeAgent(),
            **kwargs,
        )
    )


def _project(client: TestClient, name: str = "北区") -> dict[str, str]:
    return client.post("/api/projects", json={"name": name}).json()


def test_user_saves_and_lists_named_import_profile_with_source_identity(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    project = _project(client)
    payload = {
        "name": "厂商 A 流量仪",
        "source_identifier": "vendor-a-flow-v2",
        "mapping": {
            "采集时间": "timestamp",
            "测站": "point_id",
            "瞬时流量": "flow_lps",
        },
        "source_units": {"瞬时流量": "m3/h"},
        "parsing_rules": {"delimiter": ",", "decimal": "."},
    }

    created = client.post(
        f"/api/projects/{project['id']}/import-profiles",
        json=payload,
    )
    listed = client.get(f"/api/projects/{project['id']}/import-profiles")

    assert created.status_code == 201
    assert created.json() == {
        "id": created.json()["id"],
        "project_id": project["id"],
        **payload,
    }
    assert listed.status_code == 200
    assert listed.json() == [created.json()]


def test_profile_is_persistent_reusable_and_keeps_full_import_inspection(
    tmp_path: Path,
) -> None:
    first = _client(tmp_path)
    project = _project(first)
    batch = first.post(
        f"/api/projects/{project['id']}/batches", json={"name": "七月"}
    ).json()
    profile = first.post(
        f"/api/projects/{project['id']}/import-profiles",
        json={
            "name": "厂商 A",
            "source_identifier": "vendor-a",
            "mapping": {
                "采集时间": "timestamp",
                "测站": "point_id",
                "瞬时流量": "flow_lps",
            },
            "source_units": {"瞬时流量": "m3/h"},
            "parsing_rules": {"delimiter": ",", "decimal": "."},
        },
    ).json()

    client = _client(tmp_path)
    assert client.get(
        f"/api/projects/{project['id']}/import-profiles/{profile['id']}"
    ).json() == profile
    response = client.post(
        f"/api/projects/{project['id']}/batches/{batch['id']}/imports",
        params={"profile_id": profile["id"]},
        files={
            "file": (
                "vendor.csv",
                "采集时间,测站,瞬时流量\n2026-07-01,P1,3.6\n".encode(),
                "text/csv",
            )
        },
    )

    assert response.status_code == 201
    inspection = response.json()
    assert inspection["profile_id"] == profile["id"]
    assert inspection["source_identifier"] == "vendor-a"
    assert inspection["mapping"] == profile["mapping"]
    assert inspection["source_units"] == profile["source_units"]
    assert inspection["columns"] == [
        {"source": "采集时间", "field": "timestamp", "type": "string", "unit": None},
        {"source": "测站", "field": "point_id", "type": "string", "unit": None},
        {"source": "瞬时流量", "field": "flow_lps", "type": "number", "unit": "m3/h"},
    ]
    assert inspection["anomalies"] == []
    assert inspection["standard_preview"]["rows"][0]["flow_lps"] == 1.0
    assert inspection["status"] == "pending_confirmation"
    assert client.get(
        f"/api/projects/{project['id']}/batches/{batch['id']}/standard/flow"
    ).status_code == 409


class FixedMappingSuggester:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def suggest(
        self,
        *,
        source_identifier: str | None,
        columns: list[dict[str, object]],
    ) -> list[dict[str, str]]:
        self.calls.append(
            {"source_identifier": source_identifier, "columns": columns}
        )
        return [
            {"source": "when", "field": "timestamp", "confidence": "suggested"},
            {"source": "station", "field": "point_id", "confidence": "suggested"},
            {"source": "value", "field": "flow_lps", "confidence": "suggested"},
        ]


def test_llm_suggests_only_unresolved_columns_and_cannot_confirm_import(
    tmp_path: Path,
) -> None:
    suggester = FixedMappingSuggester()
    client = _client(tmp_path, mapping_suggester=suggester)
    project = _project(client)
    batch = client.post(
        f"/api/projects/{project['id']}/batches", json={"name": "未知格式"}
    ).json()
    uploaded = client.post(
        f"/api/projects/{project['id']}/batches/{batch['id']}/imports",
        params={"source_identifier": "vendor-x"},
        files={
            "file": (
                "unknown.csv",
                b"when,station,value\n2026-07-01,P1,2.5\n",
                "text/csv",
            )
        },
    ).json()

    response = client.post(
        f"/api/projects/{project['id']}/batches/{batch['id']}"
        f"/imports/{uploaded['id']}/mapping-suggestions"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "awaiting_engineer_confirmation"
    assert response.json()["candidates"] == [
        {"source": "when", "field": "timestamp", "confidence": "suggested"},
        {"source": "station", "field": "point_id", "confidence": "suggested"},
        {"source": "value", "field": "flow_lps", "confidence": "suggested"},
    ]
    assert suggester.calls[0] == {
        "source_identifier": "vendor-x",
        "columns": [
            {"source": "when", "type": "string"},
            {"source": "station", "type": "string"},
            {"source": "value", "type": "number"},
        ],
    }
    assert client.get(
        f"/api/projects/{project['id']}/batches/{batch['id']}/standard/flow"
    ).status_code == 409
    confirmation = client.put(
        f"/api/projects/{project['id']}/batches/{batch['id']}"
        f"/imports/{uploaded['id']}/mapping",
        json={
            "mapping": {
                "when": "timestamp",
                "station": "point_id",
                "value": "flow_lps",
            },
            "units": {},
        },
    )
    assert confirmation.status_code == 400
    assert "单位仍未确认" in confirmation.json()["detail"]


def test_deterministic_mapping_skips_llm_and_projects_are_isolated(
    tmp_path: Path,
) -> None:
    suggester = FixedMappingSuggester()
    client = _client(tmp_path, mapping_suggester=suggester)
    north = _project(client, "北区")
    south = _project(client, "南区")
    profile = client.post(
        f"/api/projects/{north['id']}/import-profiles",
        json={
            "name": "北区配置",
            "source_identifier": "north",
            "mapping": {"time": "timestamp"},
            "source_units": {},
            "parsing_rules": {},
        },
    ).json()
    batch = client.post(
        f"/api/projects/{north['id']}/batches", json={"name": "确定格式"}
    ).json()
    south_batch = client.post(
        f"/api/projects/{south['id']}/batches", json={"name": "南区批次"}
    ).json()
    uploaded = client.post(
        f"/api/projects/{north['id']}/batches/{batch['id']}/imports",
        files={
            "file": (
                "known.csv",
                b"time,site,flow\n2026-07-01,P1,2.5\n",
                "text/csv",
            )
        },
    ).json()

    suggestions = client.post(
        f"/api/projects/{north['id']}/batches/{batch['id']}"
        f"/imports/{uploaded['id']}/mapping-suggestions"
    )

    assert suggestions.json()["candidates"] == []
    assert suggester.calls == []
    assert client.get(
        f"/api/projects/{south['id']}/import-profiles/{profile['id']}"
    ).status_code == 404
    assert client.post(
        f"/api/projects/{south['id']}/batches/{south_batch['id']}/imports",
        params={"profile_id": profile["id"]},
        files={"file": ("x.csv", b"time\n2026-07-01\n", "text/csv")},
    ).status_code == 404
    assert client.post(
        f"/api/projects/{south['id']}/batches/{south_batch['id']}"
        f"/imports/{uploaded['id']}/mapping-suggestions"
    ).status_code == 404


def test_index_exposes_import_profile_and_candidate_workflow(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/")

    assert response.status_code == 200
    assert 'id="importProfileSelect"' in response.text
    assert 'id="importSaveProfile"' in response.text
    assert 'id="saveImportProfile"' in response.text
    assert 'id="importQuestions"' in response.text
    assert "/import-profiles" in response.text
    assert "/mapping-suggestions" in response.text
    assert "/api/standard-flow-template" in response.text


def test_reused_parsing_rules_drive_preview_and_confirmed_v1_data(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    project = _project(client)
    batch = client.post(
        f"/api/projects/{project['id']}/batches", json={"name": "欧式 CSV"}
    ).json()
    profile = client.post(
        f"/api/projects/{project['id']}/import-profiles",
        json={
            "name": "分号与小数逗号",
            "source_identifier": "vendor-eu",
            "mapping": {
                "when": "timestamp",
                "station": "point_id",
                "value": "flow_lps",
            },
            "source_units": {"value": "L/s"},
            "parsing_rules": {"delimiter": ";", "decimal": ","},
        },
    ).json()
    uploaded = client.post(
        f"/api/projects/{project['id']}/batches/{batch['id']}/imports",
        params={"profile_id": profile["id"]},
        files={
            "file": (
                "eu.csv",
                b"when;station;value\n2026-07-01;P1;2,5\n",
                "text/csv",
            )
        },
    ).json()

    assert uploaded["standard_preview"]["rows"][0]["flow_lps"] == 2.5
    confirmed = client.put(
        f"/api/projects/{project['id']}/batches/{batch['id']}"
        f"/imports/{uploaded['id']}/mapping",
        json={"mapping": profile["mapping"], "units": profile["source_units"]},
    )
    preview = client.get(
        f"/api/projects/{project['id']}/batches/{batch['id']}/standard/flow"
    ).json()

    assert confirmed.status_code == 200
    assert preview["rows"][0]["flow_lps"] == 2.5


class _FakeCompletionResponse:
    def __init__(self, content: str) -> None:
        message = type("Message", (), {"content": content})
        self.choices = [type("Choice", (), {"message": message})]


class _FakeCompletions:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.calls = 0

    def create(self, **_kwargs: object) -> _FakeCompletionResponse:
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return _FakeCompletionResponse(str(self.outcome))


def _patch_openai(monkeypatch: pytest.MonkeyPatch, outcome: object) -> _FakeCompletions:
    import openai

    completions = _FakeCompletions(outcome)

    class FakeOpenAI:
        def __init__(self, **_kwargs: object) -> None:
            self.chat = type("Chat", (), {"completions": completions})

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    monkeypatch.setattr("web.import_profiles.time.sleep", lambda _s: None)
    return completions


def test_llm_suggester_validates_candidates_against_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_openai(
        monkeypatch,
        '{"candidates": ['
        '{"source": "when", "field": "timestamp"},'
        '{"source": "value", "field": "flow_lps"},'
        '{"source": "value2", "field": "flow_lps"},'
        '{"source": "ghost", "field": "level_m"},'
        '{"source": "when", "field": "not_a_field"}'
        ']}',
    )
    from web.import_profiles import LLMMappingSuggester

    suggester = LLMMappingSuggester(model="m", base_url=None, api_key="sk")
    candidates = suggester.suggest(
        source_identifier="vendor-x",
        columns=[
            {"source": "when", "type": "string"},
            {"source": "value", "type": "number"},
            {"source": "value2", "type": "number"},
        ],
    )

    assert [(c.source, c.field) for c in candidates] == [
        ("when", "timestamp"),
        ("value", "flow_lps"),
    ]


def test_llm_suggester_returns_empty_on_llm_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    completions = _patch_openai(monkeypatch, RuntimeError("boom"))
    from web.import_profiles import LLMMappingSuggester

    suggester = LLMMappingSuggester(model="m", base_url=None, api_key="sk")
    assert suggester.suggest(
        source_identifier=None, columns=[{"source": "x", "type": "string"}]
    ) == []
    assert completions.calls == 3


def test_app_wires_suggester_by_api_key_presence(tmp_path: Path) -> None:
    from agent.deps import AgentSettings
    from web.import_profiles import LLMMappingSuggester, NoMappingSuggester

    def deps_with_key(root: Path):
        deps = make_deps(root)
        deps.settings = AgentSettings(model="m", base_url=None, api_key="sk")
        return deps

    with_key = create_app(
        tmp_path / "with",
        deps_factory=deps_with_key,
        agent_factory=lambda _deps: FakeAgent(),
    )
    without_key = create_app(
        tmp_path / "without",
        deps_factory=make_deps,
        agent_factory=lambda _deps: FakeAgent(),
    )

    assert isinstance(with_key.state.mapping_suggester, LLMMappingSuggester)
    assert isinstance(without_key.state.mapping_suggester, NoMappingSuggester)
