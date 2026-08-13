"""Deterministic, non-destructive onedir release builder."""

from __future__ import annotations

import argparse
from importlib import metadata
import json
from pathlib import Path
import platform
import subprocess
import sys

from release_manifest import scan_private_files, write_release_manifest
from version import RUNTIME_DEPENDENCIES, __version__


APP_NAME = "聊小智"
ROOT = Path(__file__).resolve().parent
DIST_ROOT = ROOT / "dist"
BUILD_ROOT = ROOT / "build"
SPEC_PATH = ROOT / f"{APP_NAME}.spec"
REQUIRED_IMPORTS = tuple(
    (
        "requests",
        "pyautogui",
        "pyperclip",
        "win32gui",
        "PIL",
        "rapidocr_onnxruntime",
        "onnxruntime",
        "cv2",
        "xlrd",
        "openpyxl",
        "pypdf",
        "enterprise_kb",
    )
)
COLLECT_ALL_PACKAGES = tuple(
    (
        "rapidocr_onnxruntime",
        "onnxruntime",
        "cv2",
        "pyautogui",
        "pyscreeze",
        "pyperclip",
        "PIL",
        "pygetwindow",
        "mouseinfo",
        "pymsgbox",
        "pyclipper",
        "shapely",
        "xlrd",
        "openpyxl",
        "pypdf",
    )
)
HIDDEN_IMPORTS = ("self_check", "release_manifest", "version", "win32timezone")

README_TEXT = """聊小智正式版使用说明

1. 请保留整个“聊小智”目录，双击“聊小智.exe”启动；不要单独复制 exe。
2. 自动回复只会发送已批准知识库中的答案原文；大模型只允许做候选排序或一致性判断，不会生成或改写答案。
3. 没有符合条件的知识答案时，程序只发送：暂未查到对应答案
4. 正式运行数据保存在当前用户 AppData，不包含在发布包中。
5. 自动发送前会复核前台窗口、聊天身份和最新问题；不确定时停止并要求人工核对。
6. “启动.bat”仅供源码开发，正式使用入口是本目录的“聊小智.exe”或桌面快捷方式。
"""


def prepare_release_dir(path: Path | str) -> Path:
    release = Path(path)
    if release.exists():
        raise FileExistsError(f"release directory already exists: {release}")
    release.mkdir(parents=True)
    return release


def _verify_build_environment(version: str) -> None:
    if version != __version__:
        raise ValueError(f"version must match version.py: {__version__}")
    if platform.python_version() != "3.11.9":
        raise RuntimeError(f"release requires Python 3.11.9, got {platform.python_version()}")
    if metadata.version("pyinstaller") != "6.21.0":
        raise RuntimeError("release requires pyinstaller 6.21.0")
    mismatches: list[str] = []
    for distribution, expected in RUNTIME_DEPENDENCIES.items():
        try:
            actual = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            actual = "missing"
        if actual != expected:
            mismatches.append(f"{distribution}={actual}, expected {expected}")
    if mismatches:
        raise RuntimeError("runtime dependency mismatch:\n" + "\n".join(mismatches))


def _write_release_notes(package_dir: Path) -> None:
    (package_dir / "使用说明.txt").write_text(README_TEXT, encoding="utf-8-sig")
    (package_dir / "BUILD-INFO.txt").write_text(
        "\n".join(
            (
                f"version={__version__}",
                f"python={platform.python_version()}",
                "format=windowed-onedir",
                "private_data_included=false",
            )
        )
        + "\n",
        encoding="utf-8",
    )


def _write_sha256s(package_dir: Path, manifest_path: Path) -> Path:
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    lines = [f"{item['sha256']}  {item['path']}" for item in document["files"]]
    target = package_dir / "SHA256SUMS.txt"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def build_release(version: str) -> Path:
    _verify_build_environment(version)
    release_root = DIST_ROOT / f"{APP_NAME}_{version}"
    work_root = BUILD_ROOT / f"{APP_NAME}_{version}"
    if release_root.exists():
        raise FileExistsError(f"release directory already exists: {release_root}")
    if work_root.exists():
        raise FileExistsError(f"build work directory already exists: {work_root}")
    prepare_release_dir(release_root)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(release_root),
        "--workpath",
        str(work_root),
        str(SPEC_PATH),
    ]
    subprocess.check_call(command, cwd=ROOT)
    package_dir = release_root / APP_NAME
    executable = package_dir / f"{APP_NAME}.exe"
    if not executable.is_file():
        raise RuntimeError("PyInstaller did not produce the expected executable")
    _write_release_notes(package_dir)
    private_files = scan_private_files(package_dir)
    if private_files:
        raise RuntimeError("release contains private files: " + ", ".join(private_files))
    manifest_path = write_release_manifest(package_dir)
    _write_sha256s(package_dir, manifest_path)
    artifact = ROOT / "artifacts" / "formal-hardening" / "2026-08-14"
    artifact.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(
        [
            str(executable),
            "--package-check",
            "--report",
            str(artifact / "packaged-check.json"),
        ],
        cwd=package_dir,
    )
    return package_dir


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="构建聊小智正式 onedir 发布包")
    parser.add_argument("--version", default=__version__)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    package_dir = build_release(args.version)
    print(f"BUILD_OK: {package_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
