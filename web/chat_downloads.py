"""Select files produced by a chat turn for download."""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path, PurePath
from typing import Any


def select_chat_artifacts(
    current: list[dict[str, Any]],
    before_paths: set[str],
) -> list[dict[str, Any]]:
    created = [item for item in current if item["path"] not in before_paths]
    return [
        item
        for item in created
        if str(item["path"]).startswith("exports/")
    ]


_DOWNLOAD_REQUEST_MARKERS = ("下载", "导出", "打包", "zip")
_CHAT_DELIVERABLE_SUFFIXES = {".png", ".jpg", ".jpeg", ".csv", ".xlsx", ".zip"}
_CHAT_DELIVERABLE_EXCLUDED_PREFIXES = (
    "standard/",
    "inputs/",
    "baseline/",
    "sessions/",
)
_REPLY_PATH_PATTERN = re.compile(
    r"[\w\-./一-鿿()]+?\.(?:png|jpe?g|csv|xlsx|zip)",
    re.IGNORECASE,
)


def requests_file_download(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in _DOWNLOAD_REQUEST_MARKERS)


def workspace_file_paths(root: Path) -> set[str]:
    if not root.exists():
        return set()
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }


def _is_chat_deliverable(rel_path: str) -> bool:
    if rel_path.startswith(_CHAT_DELIVERABLE_EXCLUDED_PREFIXES):
        return False
    if PurePath(rel_path).name == "result.json":
        return False
    return PurePath(rel_path).suffix.lower() in _CHAT_DELIVERABLE_SUFFIXES


def select_requested_downloads(
    root: Path,
    before_files: set[str],
    reply_text: str,
) -> list[dict[str, Any]]:
    created = workspace_file_paths(root) - before_files
    cited = {
        match.group(0).lstrip("./")
        for match in _REPLY_PATH_PATTERN.finditer(reply_text)
    }
    deliverable = sorted(
        path
        for path in (created | cited)
        if (
            _is_chat_deliverable(path)
            or (path in created and path.startswith("sessions/") and path.lower().endswith(".zip"))
        )
        and (root / path).is_file()
    )
    if not deliverable:
        return []
    exports = root / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    if len(deliverable) == 1:
        source = root / deliverable[0]
        target = exports / source.name
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        rel = target.relative_to(root).as_posix()
        return [{"path": rel, "name": target.name, "size": target.stat().st_size}]

    joined = " ".join(deliverable).lower()
    if "特征曲线" in joined:
        filename = "特征曲线.zip"
    elif "rdii" in joined:
        filename = "RDII分析结果.zip"
    elif "降雨" in joined:
        filename = "降雨分析结果.zip"
    else:
        filename = "本次导出结果.zip"
    bundle = exports / filename
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for rel in deliverable:
            source = root / rel
            if source.resolve() != bundle.resolve():
                archive.write(source, arcname=rel)
    rel = bundle.relative_to(root).as_posix()
    return [{"path": rel, "name": filename, "size": bundle.stat().st_size}]
