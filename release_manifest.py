"""Structured release/package/runtime validation without private payloads."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import sys
from typing import Iterator

from version import RUNTIME_DEPENDENCIES, __version__


_PRIVATE_BASE_NAMES = {
    "credentials.dat",
    "knowledge_import_stage.json",
    "managed_send.log",
    "memory.json",
    "runtime-log.salt",
    "send_ledger.json",
    "settings.json",
    "vector_memory.json",
}
_MANIFEST_EXCLUDES = {"release-manifest.json", "SHA256SUMS.txt"}
_SOURCE_EXCLUDED_DIRS = {
    ".git",
    ".superpowers",
    ".venv",
    ".workbuddy",
    "__pycache__",
    "artifacts",
    "build",
    "dist",
    "docs",
    "tests",
}


@dataclass(frozen=True)
class CheckReport:
    kind: str
    ok: bool
    missing: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    private_files: tuple[str, ...] = ()
    details: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "kind": self.kind,
            "ok": self.ok,
            "missing": list(self.missing),
            "errors": list(self.errors),
            "private_files": list(self.private_files),
            "details": dict(self.details),
        }

    def write(self, path: Path | str) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_private_file(relative: Path) -> bool:
    parts = tuple(part.casefold() for part in relative.parts)
    if "user_data" in parts:
        return True
    name = relative.name.casefold()
    return any(name == base or name.startswith(f"{base}.") for base in _PRIVATE_BASE_NAMES)


def scan_private_files(release_dir: Path | str) -> list[str]:
    root = Path(release_dir)
    if not root.exists():
        return []
    source_tree = (root / "main.py").is_file()
    found: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if source_tree and relative.parts and relative.parts[0] in _SOURCE_EXCLUDED_DIRS:
            continue
        if _is_private_file(relative):
            found.append(relative.as_posix())
    return sorted(found)


def _calibration_error(path: Path) -> str:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "calibration_json_invalid"
    required = {"version", "conversation_list_area", "top_title_area", "avatar_area", "timing"}
    if type(value) is not dict or not required.issubset(value):
        return "calibration_schema_invalid"
    return ""


def _installed_versions() -> tuple[dict[str, str], list[str]]:
    versions: dict[str, str] = {}
    errors: list[str] = []
    for distribution, expected in RUNTIME_DEPENDENCIES.items():
        try:
            actual = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            errors.append(f"dependency_missing:{distribution}")
            continue
        versions[distribution] = actual
        if actual != expected:
            errors.append(f"dependency_version:{distribution}:{actual}!={expected}")
    return versions, errors


def _manifest_errors(root: Path) -> list[str]:
    path = root / "release-manifest.json"
    if not path.exists():
        return [] if (root / "main.py").is_file() else ["release_manifest_missing"]
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        files = value["files"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
        return ["release_manifest_invalid"]
    if (
        type(value) is not dict
        or value.get("schema_version") != 1
        or value.get("version") != __version__
        or type(files) is not list
    ):
        return ["release_manifest_invalid"]
    errors: list[str] = []
    listed: set[str] = set()
    for item in files:
        if type(item) is not dict or set(item) != {"path", "size", "sha256"}:
            return ["release_manifest_invalid"]
        relative_text = str(item["path"])
        relative = Path(relative_text)
        if (
            not relative_text
            or relative.is_absolute()
            or ".." in relative.parts
            or relative_text in listed
        ):
            return ["release_manifest_invalid"]
        listed.add(relative_text)
        target = root / relative
        if not target.is_file():
            errors.append(f"manifest_missing:{item['path']}")
        elif target.stat().st_size != item["size"] or _sha256(target) != item["sha256"]:
            errors.append(f"manifest_mismatch:{item['path']}")
    actual = {
        file.relative_to(root).as_posix()
        for file in root.rglob("*")
        if file.is_file()
        and file.relative_to(root).as_posix() not in _MANIFEST_EXCLUDES
    }
    errors.extend(f"manifest_unlisted:{relative}" for relative in sorted(actual - listed))
    return errors


def run_package_check(app_dir: Path | str) -> CheckReport:
    root = Path(app_dir).resolve()
    missing: list[str] = []
    errors: list[str] = []
    calibration = root / "wecom_5_calibration.json"
    if not calibration.is_file():
        missing.append("wecom_5_calibration.json")
    else:
        error = _calibration_error(calibration)
        if error:
            errors.append(error)
    if not ((root / "聊小智.exe").is_file() or (root / "main.py").is_file()):
        missing.append("聊小智.exe|main.py")
    versions, dependency_errors = _installed_versions()
    errors.extend(dependency_errors)
    errors.extend(_manifest_errors(root))
    private_files = scan_private_files(root)
    try:
        import rapidocr_onnxruntime

        model_root = Path(rapidocr_onnxruntime.__file__).resolve().parent / "models"
        model_count = len(list(model_root.glob("*.onnx")))
        if model_count < 3:
            errors.append(f"rapidocr_models:{model_count}")
    except Exception as error:
        model_count = 0
        errors.append(f"rapidocr_import:{type(error).__name__}")
    details: dict[str, object] = {
        "version": __version__,
        "python": platform.python_version(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "dependency_versions": versions,
        "rapidocr_model_count": model_count,
    }
    return CheckReport(
        kind="package",
        ok=not missing and not errors and not private_files,
        missing=tuple(sorted(missing)),
        errors=tuple(sorted(errors)),
        private_files=tuple(private_files),
        details=details,
    )


@contextmanager
def _data_root(root: Path) -> Iterator[None]:
    previous = os.environ.get("WECHAT_ASSISTANT_DATA_DIR")
    os.environ["WECHAT_ASSISTANT_DATA_DIR"] = str(root.resolve())
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("WECHAT_ASSISTANT_DATA_DIR", None)
        else:
            os.environ["WECHAT_ASSISTANT_DATA_DIR"] = previous


def run_runtime_check(data_dir: Path | str, *, require_wecom: bool = True) -> CheckReport:
    root = Path(data_dir).resolve()
    missing: list[str] = []
    errors: list[str] = []
    details: dict[str, object] = {"data_root_exists": root.is_dir()}
    if not root.is_dir():
        missing.append("data_root")
    for name in ("settings.json", "memory.json"):
        if not (root / name).is_file():
            missing.append(name)
    if not missing:
        with _data_root(root):
            try:
                raw_settings = json.loads((root / "settings.json").read_text(encoding="utf-8"))
                if raw_settings.get("reply_source_mode", "knowledge_only") != "knowledge_only":
                    errors.append("reply_source_mode_not_knowledge_only")
            except Exception as error:
                errors.append(f"settings_raw:{type(error).__name__}")
            try:
                from settings import AppSettings

                settings = AppSettings(config_path=root / "settings.json").load()
                details["managed_mode"] = settings.managed_mode
                details["credential_readable"] = True
            except Exception as error:
                errors.append(f"settings:{type(error).__name__}")
            try:
                from memory import StyleMemory

                memory = StyleMemory(path=root / "memory.json").load()
                memory.assert_ready_for_managed_reply()
                details["knowledge_record_count"] = len(memory.examples)
                details["vector_ready"] = True
            except Exception as error:
                errors.append(f"memory_vector:{type(error).__name__}")
            try:
                from send_ledger import SendLedger

                ledger = SendLedger(root / "send_ledger.json").load()
                details["ledger_transient"] = ledger.has_transient_entries()
                if ledger.has_transient_entries():
                    errors.append("ledger_has_transient_entries")
            except Exception as error:
                errors.append(f"ledger:{type(error).__name__}")
    if require_wecom:
        try:
            from wechat_window import WeChatWindowDetector

            detected = WeChatWindowDetector().any_chat_window() is not None
            details["wecom_detected"] = detected
            if not detected:
                errors.append("wecom_window_not_detected")
        except Exception as error:
            errors.append(f"wecom_window:{type(error).__name__}")
    return CheckReport(
        kind="runtime",
        ok=not missing and not errors,
        missing=tuple(sorted(missing)),
        errors=tuple(sorted(errors)),
        details=details,
    )


def _release_files(root: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and path.relative_to(root).as_posix() not in _MANIFEST_EXCLUDES
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def write_release_manifest(release_dir: Path | str) -> Path:
    root = Path(release_dir).resolve()
    files = [
        {
            "path": path.relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in _release_files(root)
    ]
    document = {
        "schema_version": 1,
        "version": __version__,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "files": files,
    }
    path = root / "release-manifest.json"
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
