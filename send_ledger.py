"""Persistent managed-send idempotency ledger."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import threading
from typing import Literal

from app_paths import ledger_path
from atomic_json import JsonDataError, atomic_write_json, load_json


LedgerStatus = Literal["prepared", "keypress", "verified", "uncertain", "failed", "baseline"]
LEDGER_STATUSES = {"prepared", "keypress", "verified", "uncertain", "failed", "baseline"}
HANDLED_STATUSES = {"prepared", "keypress", "verified", "uncertain", "baseline"}
SCHEMA_VERSION = 1
_ENTRY_KEYS = {
    "turn_hash",
    "identity_hash",
    "record_hash",
    "answer_hash",
    "status",
    "retry_count",
    "updated_at",
    "reason",
}


class LedgerBlockingError(RuntimeError):
    """Raised when ledger durability cannot be guaranteed."""


class LedgerTransitionError(LedgerBlockingError):
    """Raised for an invalid state transition that must block sending."""


def _sha256(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _redacted_reason(value: str) -> str:
    compact = re.sub(r"[^a-z0-9_.:-]+", "_", str(value or "").casefold()).strip("_")
    return compact[:80] or "unspecified"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_ledger_document(value: object) -> None:
    if type(value) is not dict or set(value) != {"schema_version", "entries"}:
        raise ValueError("ledger root keys are invalid")
    if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("ledger schema_version is invalid")
    entries = value["entries"]
    if type(entries) is not dict:
        raise ValueError("ledger entries must be an object")
    for turn_hash, entry in entries.items():
        if type(turn_hash) is not str or not re.fullmatch(r"[0-9a-f]{64}", turn_hash):
            raise ValueError("ledger turn hash is invalid")
        if type(entry) is not dict or set(entry) != _ENTRY_KEYS:
            raise ValueError("ledger entry keys are invalid")
        for hash_key in ("turn_hash", "identity_hash", "record_hash", "answer_hash"):
            if type(entry[hash_key]) is not str or not re.fullmatch(r"[0-9a-f]{64}", entry[hash_key]):
                raise ValueError(f"ledger {hash_key} is invalid")
        if entry["turn_hash"] != turn_hash:
            raise ValueError("ledger turn hash does not match entry key")
        if type(entry["status"]) is not str or entry["status"] not in LEDGER_STATUSES:
            raise ValueError("ledger status is invalid")
        if type(entry["retry_count"]) is not int or entry["retry_count"] < 0:
            raise ValueError("ledger retry_count is invalid")
        if type(entry["updated_at"]) is not str or not entry["updated_at"]:
            raise ValueError("ledger updated_at is invalid")
        if type(entry["reason"]) is not str or len(entry["reason"]) > 80:
            raise ValueError("ledger reason is invalid")


class SendLedger:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else ledger_path()
        self._entries: dict[str, dict[str, object]] = {}
        self._loaded = False
        self._lock = threading.RLock()

    def load(self) -> "SendLedger":
        with self._lock:
            if not self.path.exists() and not self.path.with_name(f".{self.path.name}.transaction").exists():
                self._entries = {}
                self._loaded = True
                return self
            try:
                result = load_json(self.path, validate_ledger_document)
            except (JsonDataError, OSError, ValueError) as error:
                self._entries = {}
                self._loaded = False
                raise LedgerBlockingError("ledger read or recovery failed") from error
            self._entries = deepcopy(result.data["entries"])
            self._loaded = True
            return self

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def _persist_entries(self, entries: dict[str, dict[str, object]]) -> None:
        document: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "entries": entries,
        }
        try:
            atomic_write_json(self.path, document, validate_ledger_document, backup_count=5)
        except Exception as error:
            raise LedgerBlockingError("ledger write failed") from error

    def _replace_entry(self, turn_hash: str, entry: dict[str, object]) -> None:
        updated = deepcopy(self._entries)
        updated[turn_hash] = entry
        self._persist_entries(updated)
        self._entries = updated

    def contains(self, turn_key: str) -> bool:
        with self._lock:
            self._ensure_loaded()
            entry = self._entries.get(_sha256(turn_key))
            return bool(entry and entry["status"] in HANDLED_STATUSES)

    def status(self, turn_key: str) -> LedgerStatus | None:
        with self._lock:
            self._ensure_loaded()
            entry = self._entries.get(_sha256(turn_key))
            return None if entry is None else entry["status"]  # type: ignore[return-value]

    def may_retry(self, turn_key: str) -> bool:
        with self._lock:
            self._ensure_loaded()
            entry = self._entries.get(_sha256(turn_key))
            return entry is None or entry["status"] == "failed"

    def prepare(
        self,
        turn_key: str,
        identity_key: str,
        record_id: str = "",
        answer_hash: str = "",
    ) -> bool:
        """Prepare a send from raw identifiers/text; every input is hashed here.

        ``answer_hash`` is retained as a compatibility parameter name, but it is
        intentionally treated as raw input.  This API never accepts pre-hashed
        values or infers trust from a 64-hex shape.
        """
        with self._lock:
            self._ensure_loaded()
            turn_hash = _sha256(turn_key)
            previous = self._entries.get(turn_hash)
            if previous and previous["status"] in HANDLED_STATUSES:
                return False
            retry_count = int(previous["retry_count"]) + 1 if previous else 0
            entry: dict[str, object] = {
                "turn_hash": turn_hash,
                "identity_hash": _sha256(identity_key),
                "record_hash": _sha256(record_id),
                "answer_hash": _sha256(answer_hash),
                "status": "prepared",
                "retry_count": retry_count,
                "updated_at": _utc_now(),
                "reason": "prepared",
            }
            self._replace_entry(turn_hash, entry)
            return True

    def baseline(self, turn_key: str, identity_key: str) -> bool:
        with self._lock:
            self._ensure_loaded()
            turn_hash = _sha256(turn_key)
            previous = self._entries.get(turn_hash)
            if previous and previous["status"] in HANDLED_STATUSES:
                return False
            entry: dict[str, object] = {
                "turn_hash": turn_hash,
                "identity_hash": _sha256(identity_key),
                "record_hash": _sha256(""),
                "answer_hash": _sha256(""),
                "status": "baseline",
                "retry_count": int(previous["retry_count"]) if previous else 0,
                "updated_at": _utc_now(),
                "reason": "startup_baseline",
            }
            self._replace_entry(turn_hash, entry)
            return True

    def _transition(self, turn_key: str, allowed: set[str], status: LedgerStatus, reason: str) -> None:
        with self._lock:
            self._ensure_loaded()
            turn_hash = _sha256(turn_key)
            previous = self._entries.get(turn_hash)
            if previous is None or previous["status"] not in allowed:
                raise LedgerTransitionError(f"invalid ledger transition to {status}")
            entry = dict(previous)
            entry["status"] = status
            entry["updated_at"] = _utc_now()
            entry["reason"] = _redacted_reason(reason)
            self._replace_entry(turn_hash, entry)

    def mark_keypress(self, turn_key: str) -> None:
        self._transition(turn_key, {"prepared"}, "keypress", "keypress")

    def mark_verified(self, turn_key: str) -> None:
        self._transition(turn_key, {"keypress"}, "verified", "verified")

    def mark_uncertain(self, turn_key: str, reason: str) -> None:
        self._transition(turn_key, {"keypress"}, "uncertain", reason)

    def mark_failed(self, turn_key: str, reason: str) -> None:
        self._transition(turn_key, {"prepared", "keypress"}, "failed", reason)

    def has_transient_entries(self) -> bool:
        with self._lock:
            self._ensure_loaded()
            return any(entry["status"] in {"prepared", "keypress"} for entry in self._entries.values())
