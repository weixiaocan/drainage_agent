from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.deps import AgentDeps


def _file_digest(path: Path) -> dict[str, Any]:
    stat = path.stat()
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return {
        "path": str(path),
        "mtime": stat.st_mtime,
        "size": stat.st_size,
        "sha256": h.hexdigest(),
    }


def data_fingerprint(deps: AgentDeps) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(deps.paths.flow_dir.glob("*.csv")):
        files.append(_file_digest(path))
    for path in (deps.paths.rainfall_file, deps.paths.site_info_file):
        if path.exists():
            files.append(_file_digest(path))
    h = hashlib.sha256()
    for item in files:
        h.update(item["sha256"].encode("ascii"))
    return {"digest": h.hexdigest(), "files": files}


def load_manifest(deps: AgentDeps) -> dict[str, Any]:
    path = deps.paths.manifest
    if not path.exists():
        return {"version": 1, "results": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "results": {}, "error": f"manifest 解析失败: {path}"}


def save_manifest(deps: AgentDeps, manifest: dict[str, Any]) -> None:
    deps.paths.outputs.mkdir(parents=True, exist_ok=True)
    deps.paths.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def record_result(deps: AgentDeps, tool_name: str, artifacts: list[str], params: dict[str, Any] | None = None) -> None:
    manifest = load_manifest(deps)
    manifest.setdefault("version", 1)
    manifest.setdefault("results", {})
    manifest["results"][tool_name] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data_fingerprint": data_fingerprint(deps)["digest"],
        "params": params or {},
        "artifacts": artifacts,
    }
    save_manifest(deps, manifest)


def result_is_fresh(deps: AgentDeps, tool_name: str) -> bool:
    manifest = load_manifest(deps)
    item = manifest.get("results", {}).get(tool_name)
    if not item:
        return False
    return item.get("data_fingerprint") == data_fingerprint(deps)["digest"]
