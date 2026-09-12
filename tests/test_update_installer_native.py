"""Compile and run the real update installer against synthetic, isolated data."""

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "build" / "聊小智_update_installer.iss"


def _hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


@pytest.fixture(scope="module")
def installer(tmp_path_factory):
    if sys.platform != "win32":
        pytest.skip("Native installer validation requires Windows")
    compiler = Path(os.environ.get("ISCC_PATH", str(
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Inno Setup 6/ISCC.exe")))
    if not compiler.is_file():
        pytest.skip("Inno Setup compiler unavailable")
    script = SCRIPT.read_text(encoding="utf-8-sig")
    # Fail before executing the old installer: test installs must never register
    # an application or change the real user's shortcuts.
    assert "Uninstallable=no" in script
    assert "CreateUninstallRegKey=no" in script
    assert "[Icons]" not in script
    assert "[Run]" not in script
    work = tmp_path_factory.mktemp("update-installer-native")
    source = work / "package"
    (source / "_internal").mkdir(parents=True)
    (source / "聊小智.exe").write_bytes(b"SYNTHETIC-NEW-PROGRAM")
    (source / "_internal/runtime.bin").write_bytes(b"SYNTHETIC-NEW-RUNTIME")
    # Deliberately contaminated build source: the updater must copy only its
    # allowed program paths, never these synthetic settings/knowledge files.
    (source / "user_data/nested").mkdir(parents=True)
    for name in ("settings.json", "memory.json", "vector_memory.json", "credentials.dat"):
        (source / "user_data" / name).write_bytes(b"MUST-NOT-INSTALL")
        (source / name).write_bytes(b"MUST-NOT-INSTALL")
    (source / "user_data/nested/custom.db").write_bytes(b"MUST-NOT-INSTALL")
    cmd = [str(compiler), "/Q", "/DAppVersion=2099.01.01.1",
           "/DAppVersionNumeric=2099.1.1.1", f"/DPackageSource={source}",
           f"/DInstallerOutputDir={work / 'output'}",
           "/DInstallerAppId={{" + str(uuid.uuid4()).upper() + "}", str(SCRIPT)]
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    return work / "output/聊小智_更新包_2099.01.01.1.exe"


def _install(installer, target, log):
    result = subprocess.run([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES",
                             "/SP-", "/NORESTART", "/NOCLOSEAPPLICATIONS", "/NOICONS",
                             f"/DIR={target}", f"/LOG={log}"], timeout=90)
    return result.returncode


def _old_install(target, label):
    data = target / "user_data"
    (data / "nested").mkdir(parents=True)
    (target / "_internal").mkdir()
    (target / "聊小智.exe").write_bytes(b"SYNTHETIC-OLD-PROGRAM")
    (target / "_internal/runtime.bin").write_bytes(b"SYNTHETIC-OLD-RUNTIME")
    files = {"settings.json": '{"api_key":"SYNTHETIC-' + label + '-DO-NOT-SHIP"}',
             "memory.json": '{"examples":[{"question":"合成问题","reply":"合成答案"}]}',
             "vector_memory.json": '{"items":[{"vector":{"1":0.25}}]}',
             "credentials.dat": "SYNTHETIC-ENCRYPTED-BYTES-" + label,
             "nested/custom.db": "SYNTHETIC-CUSTOM-DATA-" + label}
    for name, value in files.items():
        (data / name).write_bytes(value.encode("utf-8"))
    (target / "settings.json").write_bytes(b"LEGACY-ROOT-SETTINGS")
    return data


@pytest.mark.parametrize("label", ["USER-A", "USER-B"])
def test_update_preserves_each_users_entire_data_and_backups(installer, tmp_path, label):
    target = tmp_path / "原程序目录"
    data = _old_install(target, label)
    before = _hashes(data)
    assert _install(installer, target, tmp_path / "install.log") == 0
    assert "Original user data unchanged: SHA256 verified for every file." in (
        tmp_path / "install.log").read_text(encoding="utf-8-sig", errors="replace")
    assert (target / "聊小智.exe").read_bytes() == b"SYNTHETIC-NEW-PROGRAM"
    assert (target / "_internal/runtime.bin").read_bytes() == b"SYNTHETIC-NEW-RUNTIME"
    assert _hashes(data) == before
    assert (target / "settings.json").read_bytes() == b"LEGACY-ROOT-SETTINGS"
    assert not (target / "memory.json").exists()
    backups = list((target / "update_backups").glob("*/user_data"))
    assert len(backups) == 1 and _hashes(backups[0]) == before
    assert not list(target.glob("unins*.exe"))
    # A second update must preserve both data and the first backup.
    assert _install(installer, target, tmp_path / "repeat.log") == 0
    backups = list((target / "update_backups").glob("*/user_data"))
    assert len(backups) == 2
    assert all(_hashes(backup) == before for backup in backups)
    assert _hashes(data) == before


@pytest.mark.parametrize("kind", ["new", "program_without_data", "settings_without_knowledge"])
def test_update_refuses_wrong_or_empty_install_location(installer, tmp_path, kind):
    target = tmp_path / "错误目录"
    if kind != "new":
        target.mkdir()
        (target / "聊小智.exe").write_bytes(b"SYNTHETIC-OLD-PROGRAM")
    if kind == "settings_without_knowledge":
        (target / "user_data").mkdir()
        (target / "user_data/settings.json").write_bytes(b"{}")
    before = _hashes(target)
    assert _install(installer, target, tmp_path / "rejected.log") != 0
    assert _hashes(target) == before
    assert not (target / "update_backups").exists()


def test_backup_failure_leaves_program_and_data_untouched(installer, tmp_path):
    target = tmp_path / "old"
    _old_install(target, "BACKUP-FAILURE")
    (target / "update_backups").write_bytes(b"A-FILE-BLOCKS-BACKUP-DIRECTORY")
    before = _hashes(target)
    assert _install(installer, target, tmp_path / "backup-failed.log") != 0
    assert _hashes(target) == before
