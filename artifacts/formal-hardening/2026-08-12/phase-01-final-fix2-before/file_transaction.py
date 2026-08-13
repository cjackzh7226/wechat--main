"""Crash-recoverable replacement of one primary file and five backups."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import uuid
from typing import Callable, Sequence


BACKUP_COUNT = 5
FaultHook = Callable[[str, str], None]


class TransactionRecoveryError(RuntimeError):
    """Raised when a durable transaction journal cannot be safely recovered."""


_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}


def _path_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(str(path)))
    with _LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


def _candidate_paths(path: Path) -> list[Path]:
    return [path, *[path.with_name(f"{path.name}.bak.{index}") for index in range(1, BACKUP_COUNT + 1)]]


def _transaction_dir(path: Path) -> Path:
    return path.with_name(f".{path.name}.transaction")


def _prep_pattern(path: Path) -> str:
    return f".{path.name}.transaction.prep-*"


def _call_fault(hook: FaultHook | None, operation: str, target: str) -> None:
    if hook is not None:
        hook(operation, target)


def _write_fsynced_file(path: Path, payload: bytes) -> None:
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _replace_target(path: Path, payload: bytes, label: str, fault_hook: FaultHook | None) -> None:
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".txn.tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            _call_fault(fault_hook, "write", label)
            stream.write(payload)
            stream.flush()
            _call_fault(fault_hook, "fsync", label)
            os.fsync(stream.fileno())
        _call_fault(fault_hook, "replace", label)
        os.replace(temp_name, path)
        _flush_directory(path.parent)
    finally:
        try:
            Path(temp_name).unlink(missing_ok=True)
        except OSError:
            pass


def _replace_without_fault(path: Path, payload: bytes) -> None:
    _replace_target(path, payload, "recovery", None)


def _delete_target(path: Path, label: str, fault_hook: FaultHook | None) -> None:
    _call_fault(fault_hook, "delete", label)
    path.unlink(missing_ok=True)
    _flush_directory(path.parent)


def _flush_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        str(path),
        0x80000000,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x02000000,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle in (None, invalid_handle):
        raise OSError(ctypes.get_last_error(), f"could not open directory for flush: {path}")
    try:
        if not kernel32.FlushFileBuffers(ctypes.c_void_p(handle)):
            error_code = ctypes.get_last_error()
            if error_code not in {1, 5}:
                raise OSError(error_code, f"could not flush directory: {path}")
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def _remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _clean_stale_preparations(path: Path) -> None:
    for prep in path.parent.glob(_prep_pattern(path)):
        _remove_tree(prep)


def recover_transaction(path: Path) -> None:
    """Restore an uncommitted journal or finish cleanup of a committed one."""
    path = Path(path)
    if not path.parent.exists():
        return
    with _path_lock(path):
        _clean_stale_preparations(path)
        journal = _transaction_dir(path)
        if not journal.exists():
            return
        committed = journal / "COMMITTED"
        if committed.exists():
            try:
                _remove_tree(journal)
                _flush_directory(path.parent)
            except OSError:
                return
            return

        try:
            manifest = json.loads((journal / "manifest.json").read_text(encoding="utf-8"))
            expected_names = [candidate.name for candidate in _candidate_paths(path)]
            if (
                type(manifest) is not dict
                or manifest.get("version") != 1
                or manifest.get("target") != path.name
                or type(manifest.get("candidates")) is not list
                or [item.get("name") for item in manifest["candidates"]] != expected_names
            ):
                raise ValueError("transaction manifest does not match target candidates")
            for index, item in enumerate(manifest["candidates"]):
                if type(item) is not dict or type(item.get("existed")) is not bool:
                    raise ValueError("transaction candidate entry is invalid")
                target = _candidate_paths(path)[index]
                if item["existed"]:
                    snapshot_name = item.get("snapshot")
                    if type(snapshot_name) is not str or Path(snapshot_name).name != snapshot_name:
                        raise ValueError("transaction snapshot name is invalid")
                    _replace_without_fault(target, (journal / snapshot_name).read_bytes())
                else:
                    target.unlink(missing_ok=True)
                    _flush_directory(target.parent)
            _remove_tree(journal)
            _flush_directory(path.parent)
        except Exception as error:
            raise TransactionRecoveryError(f"could not recover transaction for {path}") from error


def transactional_replace_with_backups(
    path: Path,
    payload: bytes,
    valid_snapshots: Sequence[bytes],
    *,
    fault_hook: FaultHook | None = None,
) -> None:
    """Replace a primary and rotate five validated snapshots as one recoverable transaction."""
    path = Path(path)
    if type(payload) is not bytes:
        raise TypeError("payload must be bytes")
    if len(valid_snapshots) > BACKUP_COUNT:
        raise ValueError("at most five backup snapshots are allowed")
    if any(type(snapshot) is not bytes for snapshot in valid_snapshots):
        raise TypeError("backup snapshots must be bytes")
    path.parent.mkdir(parents=True, exist_ok=True)

    with _path_lock(path):
        recover_transaction(path)
        journal = _transaction_dir(path)
        prep = path.with_name(f".{path.name}.transaction.prep-{uuid.uuid4().hex}")
        published = False
        try:
            prep.mkdir(parents=False, exist_ok=False)
            manifest_entries: list[dict[str, object]] = []
            for index, candidate in enumerate(_candidate_paths(path)):
                entry: dict[str, object] = {"name": candidate.name, "existed": candidate.exists()}
                if candidate.exists():
                    snapshot_name = f"old-{index}.bin"
                    _write_fsynced_file(prep / snapshot_name, candidate.read_bytes())
                    entry["snapshot"] = snapshot_name
                manifest_entries.append(entry)
            _write_fsynced_file(prep / "new-primary.bin", payload)
            for index, snapshot in enumerate(valid_snapshots, 1):
                _write_fsynced_file(prep / f"new-backup-{index}.bin", snapshot)
            manifest = {
                "version": 1,
                "target": path.name,
                "candidates": manifest_entries,
            }
            _write_fsynced_file(
                prep / "manifest.json",
                json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            )
            _flush_directory(prep)
            os.replace(prep, journal)
            published = True
            _flush_directory(path.parent)

            try:
                for index in range(1, BACKUP_COUNT + 1):
                    backup = path.with_name(f"{path.name}.bak.{index}")
                    label = f"backup:{index}"
                    if index <= len(valid_snapshots):
                        snapshot = (journal / f"new-backup-{index}.bin").read_bytes()
                        _replace_target(backup, snapshot, label, fault_hook)
                    else:
                        _delete_target(backup, label, fault_hook)
                _replace_target(path, (journal / "new-primary.bin").read_bytes(), "primary", fault_hook)
                _write_fsynced_file(journal / "COMMITTED", b"committed\n")
                _flush_directory(journal)
                _flush_directory(path.parent)
            except Exception:
                recover_transaction(path)
                raise

            try:
                _remove_tree(journal)
                _flush_directory(path.parent)
            except OSError:
                pass
        finally:
            if not published:
                try:
                    _remove_tree(prep)
                except OSError:
                    pass
