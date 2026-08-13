"""Windows DPAPI-backed credential persistence with durable backups."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Mapping

import atomic_json
from app_paths import credential_path
from file_transaction import BACKUP_COUNT, TransactionRecoveryError


ProtectCallable = Callable[[bytes], bytes]
UnprotectCallable = Callable[[bytes], bytes]


class CredentialStoreError(RuntimeError):
    """Raised when an existing credential store has no readable copy."""


def _dpapi_protect(payload: bytes) -> bytes:
    import win32crypt

    return win32crypt.CryptProtectData(
        payload,
        "WeChatStyleReplyAssistant credentials",
        None,
        None,
        None,
        0,
    )


def _dpapi_unprotect(blob: bytes) -> bytes:
    import win32crypt

    _description, payload = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
    return payload


class CredentialStore:
    def __init__(
        self,
        path: Path | None = None,
        *,
        protect: ProtectCallable | None = None,
        unprotect: UnprotectCallable | None = None,
        backup_count: int = 5,
    ) -> None:
        self.path = Path(path) if path is not None else credential_path()
        self._protect = protect or _dpapi_protect
        self._unprotect = unprotect or _dpapi_unprotect
        if type(backup_count) is not int or backup_count != BACKUP_COUNT:
            raise ValueError("backup_count must be exactly 5")
        self.backup_count = BACKUP_COUNT

    def _decode_blob(self, blob: bytes) -> dict[str, str]:
        payload = self._unprotect(blob)
        if not isinstance(payload, bytes):
            raise TypeError("unprotect must return bytes")
        value = json.loads(payload.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("credential payload must be an object")
        if any(type(key) is not str or type(secret) is not str for key, secret in value.items()):
            raise ValueError("credential names and values must be strings")
        return value

    def _candidates(self) -> list[Path]:
        return [
            self.path,
            *(self.path.with_name(f"{self.path.name}.bak.{index}") for index in range(1, self.backup_count + 1)),
        ]

    def load(self) -> dict[str, str]:
        try:
            atomic_json.recover_atomic_write(self.path)
        except TransactionRecoveryError as error:
            raise CredentialStoreError("Credential transaction recovery failed") from error
        existing = False
        reasons: list[str] = []
        for candidate in self._candidates():
            try:
                blob = candidate.read_bytes()
                existing = True
                return self._decode_blob(blob)
            except FileNotFoundError:
                continue
            except Exception as error:
                existing = True
                reasons.append(f"{candidate.name}: {type(error).__name__}: {error}")
        if not existing:
            return {}
        raise CredentialStoreError("No valid credential blob: " + "; ".join(reasons))

    def _valid_snapshots(self) -> list[bytes]:
        snapshots: list[bytes] = []
        for candidate in self._candidates():
            try:
                blob = candidate.read_bytes()
                self._decode_blob(blob)
            except Exception:
                continue
            snapshots.append(blob)
            if len(snapshots) == self.backup_count:
                break
        return snapshots

    def save(self, keys: Mapping[str, str]) -> None:
        values = dict(keys)
        if any(type(key) is not str or type(secret) is not str for key, secret in values.items()):
            raise ValueError("credential names and values must be strings")
        payload = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        protected = self._protect(payload)
        if not isinstance(protected, bytes):
            raise TypeError("protect must return bytes")
        self._decode_blob(protected)

        snapshots = self._valid_snapshots()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json.recover_atomic_write(self.path)
        atomic_json.atomic_write_bytes(self.path, protected, snapshots)
