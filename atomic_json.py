"""Validated JSON persistence with recoverable backups."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Callable

from file_transaction import (
    BACKUP_COUNT,
    TransactionRecoveryError,
    recover_transaction,
    transactional_replace_with_backups,
)


JsonValidator = Callable[[object], None]
_TRANSACTION_FAULT_HOOK = None


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
    try:
        recover_atomic_write(path)
    except TransactionRecoveryError as error:
        raise JsonDataError(path, [(_transaction_path(path), f"TransactionRecoveryError: {error}")]) from error
    candidates = [path, *(path.with_name(f"{path.name}.bak.{index}") for index in range(1, BACKUP_COUNT + 1))]
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


def _valid_snapshots(path: Path, validator: JsonValidator) -> list[bytes]:
    candidates = [path, *(path.with_name(f"{path.name}.bak.{index}") for index in range(1, BACKUP_COUNT + 1))]
    snapshots: list[bytes] = []
    for candidate in candidates:
        try:
            snapshots.append(_read_valid_snapshot(candidate, validator))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
        if len(snapshots) == BACKUP_COUNT:
            break
    return snapshots


def _transaction_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.transaction")


def recover_atomic_write(path: Path) -> None:
    recover_transaction(Path(path))


def _invoke_transaction_fault(operation: str, target: str) -> None:
    hook = _TRANSACTION_FAULT_HOOK
    if hook is not None:
        hook(operation, target)


def atomic_write_bytes(
    path: Path,
    payload: bytes,
    valid_snapshots: list[bytes] | None = None,
    *,
    snapshot_loader: Callable[[], list[bytes]] | None = None,
) -> None:
    transactional_replace_with_backups(
        Path(path),
        payload,
        valid_snapshots,
        snapshot_loader=snapshot_loader,
        fault_hook=_invoke_transaction_fault,
    )


def atomic_write_json(
    path: Path,
    data: dict[str, object],
    validator: JsonValidator,
    backup_count: int = 5,
) -> None:
    """Validate, durably stage, and atomically replace a JSON document."""
    if type(backup_count) is not int or backup_count != BACKUP_COUNT:
        raise ValueError("backup_count must be exactly 5")
    validator(data)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    atomic_write_bytes(
        path,
        payload,
        snapshot_loader=lambda: _valid_snapshots(path, validator),
    )
