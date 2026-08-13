from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

import pytest

import app_paths


_SESSION_ROOT = Path(tempfile.mkdtemp(prefix="liaoxiaozhi-tests-"))
_SESSION_DATA_ROOT = _SESSION_ROOT / "appdata"
_SESSION_TEMP_ROOT = _SESSION_ROOT / "temp"
_SESSION_PYCACHE_ROOT = _SESSION_ROOT / "pycache"
_ORIGINAL_ENV = {
    name: os.environ.get(name)
    for name in (app_paths.TEST_OVERRIDE, "TEMP", "TMP", "PYTHONPYCACHEPREFIX")
}
_ORIGINAL_PYCACHE_PREFIX = sys.pycache_prefix


def pytest_configure(config):
    """Install data/temp/cache isolation before pytest imports test modules."""
    del config
    for root in (_SESSION_DATA_ROOT, _SESSION_TEMP_ROOT, _SESSION_PYCACHE_ROOT):
        root.mkdir(parents=True, exist_ok=True)
    os.environ[app_paths.TEST_OVERRIDE] = str(_SESSION_DATA_ROOT)
    os.environ["TEMP"] = str(_SESSION_TEMP_ROOT)
    os.environ["TMP"] = str(_SESSION_TEMP_ROOT)
    os.environ["PYTHONPYCACHEPREFIX"] = str(_SESSION_PYCACHE_ROOT)
    tempfile.tempdir = str(_SESSION_TEMP_ROOT)
    sys.pycache_prefix = str(_SESSION_PYCACHE_ROOT)


def pytest_unconfigure(config):
    del config
    for name, value in _ORIGINAL_ENV.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    tempfile.tempdir = None
    sys.pycache_prefix = _ORIGINAL_PYCACHE_PREFIX


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


def production_file_hashes(root: Path) -> dict[str, str]:
    snapshot = snapshot_data_directory(root)
    return {
        str(item["relative_path"]): str(item["sha256"])
        for item in snapshot["files"]
    }


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


@pytest.fixture
def isolated_data_root() -> Path:
    return _SESSION_DATA_ROOT
