"""Validated JSON persistence with recoverable backups."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from typing import Callable


JsonValidator = Callable[[object], None]


class JsonDataError(RuntimeError):
    """Raised when no valid JSON candidate is available."""

    def __init__(self, path: Path, reasons: list[tuple[Path, str]]):
        self.path = path
        self.reasons = tuple(reasons)
        details = "; ".join(f"{candidate}: {reason}" for candidate, reason in reasons)
        super().__init__(f"No valid JSON data for {path}: {details}")


@dataclass(frozen=True)
class JsonLoadResult:
    data: dict[str, object]
    source: Path
    recovered: bool


def _read_valid_json(path: Path, validator: JsonValidator) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    validator(value)
    return value


def load_json(path: Path, validator: JsonValidator) -> JsonLoadResult:
    """Load the primary JSON file or the newest valid backup."""
    path = Path(path)
    candidates = [path, *(path.with_name(f"{path.name}.bak.{index}") for index in range(1, 6))]
    reasons: list[tuple[Path, str]] = []
    for candidate in candidates:
        try:
            return JsonLoadResult(
                data=_read_valid_json(candidate, validator),
                source=candidate,
                recovered=candidate != path,
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            reasons.append((candidate, f"{type(error).__name__}: {error}"))
    raise JsonDataError(path, reasons)


def _read_valid_snapshot(path: Path, validator: JsonValidator) -> bytes:
    snapshot = path.read_bytes()
    value = json.loads(snapshot.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    validator(value)
    return snapshot


def _replace_backup(path: Path, snapshot: bytes) -> None:
    descriptor, temp_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(snapshot)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _rotate_backups(path: Path, validator: JsonValidator, backup_count: int) -> None:
    if backup_count <= 0:
        return
    candidates = [path, *(path.with_name(f"{path.name}.bak.{index}") for index in range(1, backup_count + 1))]
    snapshots: list[bytes] = []
    for candidate in candidates:
        try:
            snapshots.append(_read_valid_snapshot(candidate, validator))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
        if len(snapshots) == backup_count:
            break
    for index, snapshot in enumerate(snapshots, start=1):
        _replace_backup(path.with_name(f"{path.name}.bak.{index}"), snapshot)
    for index in range(len(snapshots) + 1, backup_count + 1):
        path.with_name(f"{path.name}.bak.{index}").unlink(missing_ok=True)


def atomic_write_json(
    path: Path,
    data: dict[str, object],
    validator: JsonValidator,
    backup_count: int = 5,
) -> None:
    """Validate, durably stage, and atomically replace a JSON document."""
    validator(data)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    descriptor, temp_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _rotate_backups(path, validator, backup_count)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
