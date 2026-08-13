from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

import app_paths


def snapshot_data_directory(root: Path) -> dict[str, object]:
    root = Path(root)
    files: list[dict[str, object]] = []
    if root.exists():
        for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: str(item)):
            stat = path.stat()
            files.append(
                {
                    "relative_path": path.relative_to(root).as_posix(),
                    "size": stat.st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "mtime_ns": stat.st_mtime_ns,
                }
            )
    return {"root": str(root), "exists": root.exists(), "files": files}


def _write_manifest(path: Path, manifest: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture(scope="session", autouse=True)
def isolate_entire_test_suite_data_root(tmp_path_factory):
    appdata = os.environ.get("APPDATA", "").strip()
    formal_root = Path(appdata) / app_paths.APP_NAME if appdata else Path.home() / ".wechat_style_reply_assistant"
    artifact_dir = Path(__file__).resolve().parents[1] / "artifacts" / "formal-hardening" / "2026-08-12"
    before_path = artifact_dir / "phase-01-formal-appdata-before.json"
    after_path = artifact_dir / "phase-01-formal-appdata-after.json"
    before = snapshot_data_directory(formal_root)
    _write_manifest(before_path, before)

    previous_override = os.environ.get(app_paths.TEST_OVERRIDE)
    isolated_root = tmp_path_factory.mktemp("wechat-assistant-suite-data")
    os.environ[app_paths.TEST_OVERRIDE] = str(isolated_root)
    try:
        yield isolated_root
    finally:
        if previous_override is None:
            os.environ.pop(app_paths.TEST_OVERRIDE, None)
        else:
            os.environ[app_paths.TEST_OVERRIDE] = previous_override
        after = snapshot_data_directory(formal_root)
        _write_manifest(after_path, after)
        assert after == before, "formal AppData changed during the test suite"


@pytest.fixture(autouse=True)
def isolate_each_test_data_root(tmp_path, monkeypatch):
    isolated_root = tmp_path / "isolated-app-data"
    monkeypatch.setenv(app_paths.TEST_OVERRIDE, str(isolated_root))
    return isolated_root
