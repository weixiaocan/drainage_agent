"""Monitoring data import, field mapping and auxiliary data routes."""

from __future__ import annotations

import json
import io
from dataclasses import asdict
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse

from analysis.io.standard import STANDARD_FLOW_COLUMNS
from web.schemas import BatchImportMappingRequest, ImportMappingRequest
from web.uploads import ALLOWED_SITE_EXTENSIONS, read_upload
from web.workspace import invalidate_derived_state


def register(app: FastAPI) -> None:
    @app.post(
        "/api/projects/{project_id}/batches/{batch_id}/imports",
        status_code=201,
    )
    async def import_batch_data(
        project_id: str,
        batch_id: str,
        file: UploadFile = File(...),
        profile_id: str | None = None,
        source_identifier: str | None = None,
    ) -> dict[str, object]:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        profile = None
        if profile_id:
            profile = app.state.import_profiles.get(project_id, profile_id)
            if profile is None:
                raise HTTPException(status_code=404, detail="导入配置不存在")
        try:
            inspection = app.state.data_importer.inspect_upload(
                project_id,
                batch_id,
                file.filename or "",
                await read_upload(file),
                profile_id=profile.id if profile else None,
                source_identifier=(
                    profile.source_identifier if profile else source_identifier
                ),
                profile_mapping=profile.mapping if profile else None,
                profile_units=profile.source_units if profile else None,
                parsing_rules=profile.parsing_rules if profile else None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return inspection.as_dict()

    @app.post(
        "/api/projects/{project_id}/batches/{batch_id}/batch-imports",
        status_code=201,
    )
    async def import_batch_files(
        project_id: str,
        batch_id: str,
        files: list[UploadFile] = File(...),
    ) -> dict[str, object]:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        if not files:
            raise HTTPException(status_code=400, detail="请至少选择一个监测数据文件")
        inspections = []
        try:
            for upload in files:
                inspections.append(
                    app.state.data_importer.inspect_upload(
                        project_id,
                        batch_id,
                        upload.filename or "",
                        await read_upload(upload),
                    ).as_dict()
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"imports": inspections}

    @app.post(
        "/api/projects/{project_id}/batches/{batch_id}"
        "/imports/{import_id}/mapping-suggestions"
    )
    def suggest_import_mapping(
        project_id: str,
        batch_id: str,
        import_id: str,
    ) -> dict[str, object]:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        inspection = app.state.data_importer.inspection(
            project_id, batch_id, import_id
        )
        if inspection is None:
            raise HTTPException(status_code=404, detail="导入记录不存在")
        unresolved = [
            {"source": column["source"], "type": column["type"]}
            for column in inspection["columns"]
            if column["field"] is None
        ]
        candidates: list[dict[str, object]] = []
        if unresolved:
            suggested = app.state.mapping_suggester.suggest(
                source_identifier=inspection.get("source_identifier"),
                columns=unresolved,
            )
            candidates = [
                item if isinstance(item, dict) else asdict(item)
                for item in suggested
                if (
                    item.get("source") if isinstance(item, dict) else item.source
                )
                in {column["source"] for column in unresolved}
                and (
                    item.get("field") if isinstance(item, dict) else item.field
                )
                in STANDARD_FLOW_COLUMNS
            ]
        return {
            "status": "awaiting_engineer_confirmation",
            "candidates": candidates,
        }

    @app.get(
        "/api/projects/{project_id}/batches/{batch_id}/imports/{import_id}/raw"
    )
    def download_raw_batch_data(
        project_id: str,
        batch_id: str,
        import_id: str,
    ) -> FileResponse:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        path = app.state.data_importer.raw_file(project_id, batch_id, import_id)
        if path is None:
            raise HTTPException(status_code=404, detail="原始监测数据不存在")
        return FileResponse(path, filename=path.name)

    @app.put(
        "/api/projects/{project_id}/batches/{batch_id}/imports/{import_id}/mapping"
    )
    def confirm_import_mapping(
        project_id: str,
        batch_id: str,
        import_id: str,
        request: ImportMappingRequest,
    ) -> dict[str, object]:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        try:
            result = app.state.data_importer.confirm_mapping(
                project_id,
                batch_id,
                import_id,
                request.mapping,
                request.units,
            )
            if result["replaced_existing"]:
                invalidate_derived_state(app, project_id, batch_id)
            result["derived_state_reset"] = bool(result["replaced_existing"])
            return result
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put(
        "/api/projects/{project_id}/batches/{batch_id}/batch-imports/mapping"
    )
    def confirm_batch_import_mappings(
        project_id: str,
        batch_id: str,
        request: BatchImportMappingRequest,
    ) -> dict[str, object]:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        try:
            result = app.state.data_importer.confirm_batch_mappings(
                project_id,
                batch_id,
                [item.model_dump() for item in request.imports],
            )
            if result["replaced_existing"]:
                invalidate_derived_state(app, project_id, batch_id)
            result["derived_state_reset"] = bool(result["replaced_existing"])
            return result
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/standard-flow-template")
    def download_standard_flow_template() -> Response:
        content = (
            "数据时间,设备编号,点位编号,流量(L/s),液位(m),流速(m/s)\n"
            "2026-01-01 00:00:00,D001,W1,12.5,1.23,0.45\n"
        )
        return Response(
            content=content.encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition":
                    'attachment; filename="standard_flow_template.csv"'
            },
        )

    @app.get("/api/projects/{project_id}/batches/{batch_id}/standard/flow")
    def get_standard_flow(project_id: str, batch_id: str) -> dict[str, object]:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        preview = app.state.data_importer.standard_preview(project_id, batch_id)
        if preview is None:
            raise HTTPException(status_code=409, detail="标准数据尚未确认生成")
        return preview

    def _auxiliary_frame(content: bytes, filename: str) -> pd.DataFrame:
        suffix = Path(filename).suffix.lower()
        if suffix == ".csv":
            for encoding in ("utf-8-sig", "utf-8", "gb18030"):
                try:
                    return pd.read_csv(io.BytesIO(content), encoding=encoding)
                except UnicodeDecodeError:
                    continue
            raise ValueError("CSV 编码无法识别")
        if suffix in ALLOWED_SITE_EXTENSIONS:
            return pd.read_excel(io.BytesIO(content))
        raise ValueError("辅助数据文件类型不受支持")

    def _auxiliary_columns(frame: pd.DataFrame, kind: str) -> list[dict[str, str | None]]:
        aliases = {
            "rainfall": {
                "timestamp": {
                    "timestamp", "time", "date", "时间", "日期", "数据时间"
                },
                "rain_mm": {"rain", "rain_mm", "雨量", "降雨量", "降雨量(mm)", "日降雨量(mm)"},
            },
            "sites": {
                "point_id": {
                    "point_id", "点位", "点位编号", "监测点位",
                    "安装点位", "安装监测点位", "监测点编号",
                },
                "device_type": {"device_type", "设备类型", "类型"},
                "shape": {"shape", "形状", "管道形状", "管形状", "绑定管形状"},
                "diameter_m": {"diameter_m", "管径", "管径(m)", "管径（m）"},
                "well_depth_m": {"well_depth_m", "井深", "井深(m)", "井深（m）"},
                "install_time": {
                    "install_time", "设备安装时间", "安装时间", "监测设备安装时间"
                },
                "pipe_type": {"pipe_type", "管道类型", "管材", "管网类型"},
            },
        }[kind]
        return [
            {
                "source": str(source),
                "field": next(
                    (field for field, names in aliases.items() if str(source).strip() in names),
                    None,
                ),
                "type": str(frame[source].dtype),
            }
            for source in frame.columns
        ]

    @app.post(
        "/api/projects/{project_id}/batches/{batch_id}/auxiliary/inspect"
    )
    async def inspect_auxiliary_data(
        project_id: str,
        batch_id: str,
        rainfall_file: UploadFile | None = File(default=None),
        site_info_file: UploadFile | None = File(default=None),
    ) -> dict[str, object]:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        result: dict[str, object] = {}
        try:
            if rainfall_file and rainfall_file.filename:
                content = await read_upload(rainfall_file)
                frame = _auxiliary_frame(content, rainfall_file.filename)
                result["rainfall"] = {
                    "filename": rainfall_file.filename,
                    "row_count": len(frame),
                    "columns": _auxiliary_columns(frame, "rainfall"),
                }
            if site_info_file and site_info_file.filename:
                content = await read_upload(site_info_file)
                frame = _auxiliary_frame(content, site_info_file.filename)
                result["sites"] = {
                    "filename": site_info_file.filename,
                    "row_count": len(frame),
                    "columns": _auxiliary_columns(frame, "sites"),
                }
        except (ValueError, ImportError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not result:
            raise HTTPException(status_code=400, detail="请至少选择一个辅助数据文件")
        return result

    @app.post(
        "/api/projects/{project_id}/batches/{batch_id}/auxiliary/confirm"
    )
    async def confirm_auxiliary_data(
        project_id: str,
        batch_id: str,
        mappings: str = Form(...),
        rainfall_file: UploadFile | None = File(default=None),
        site_info_file: UploadFile | None = File(default=None),
    ) -> dict[str, object]:
        if app.state.projects.get_batch(project_id, batch_id) is None:
            raise HTTPException(status_code=404, detail="分析批次不存在")
        try:
            mapping_data = json.loads(mappings)
            standard_root = app.state.data_importer.standard_flow_path(
                project_id, batch_id
            ).parent
            standard_root.mkdir(parents=True, exist_ok=True)
            saved: list[str] = []
            source_filenames: dict[str, str] = {}
            specs = [
                (
                    "rainfall",
                    rainfall_file,
                    ["timestamp", "rain_mm"],
                    ["timestamp", "rain_mm"],
                    "rainfall.csv",
                ),
                (
                    "sites",
                    site_info_file,
                    [
                        "point_id",
                        "device_type",
                        "shape",
                        "diameter_m",
                        "well_depth_m",
                        "install_time",
                        "pipe_type",
                    ],
                    ["point_id", "diameter_m", "well_depth_m"],
                    "sites.csv",
                ),
            ]
            for kind, upload, fields, required, target_name in specs:
                if upload is None or not upload.filename:
                    continue
                frame = _auxiliary_frame(await read_upload(upload), upload.filename)
                mapping = mapping_data.get(kind, {})
                missing = [field for field in required if field not in mapping.values()]
                if missing:
                    raise ValueError(f"{upload.filename} 缺少字段匹配: {', '.join(missing)}")
                normalized = pd.DataFrame(
                    {
                        field: (
                            frame[
                                next(
                                    source
                                    for source, target in mapping.items()
                                    if target == field
                                )
                            ]
                            if field in mapping.values()
                            else pd.Series([""] * len(frame), index=frame.index)
                        )
                        for field in fields
                    }
                )
                normalized.to_csv(standard_root / target_name, index=False, encoding="utf-8")
                saved.append(target_name)
                source_filenames[kind] = upload.filename
        except (ValueError, KeyError, json.JSONDecodeError, StopIteration) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not saved:
            raise HTTPException(
                status_code=400,
                detail="辅助数据文件已失效，请重新选择后再确认",
            )
        manifest_path = standard_root / "auxiliary_manifest.json"
        existing_manifest = {}
        if manifest_path.is_file():
            try:
                existing_manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                existing_manifest = {}
        existing_manifest.update(source_filenames)
        manifest_path.write_text(
            json.dumps(existing_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {"saved": saved, "message": "辅助数据已确认并生成标准文件。"}
