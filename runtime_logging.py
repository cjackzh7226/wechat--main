from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import re
import secrets
import threading
from typing import Mapping

from atomic_json import atomic_write_bytes, recover_atomic_write


_AUDIT_FIELDS = {
    "record_id",
    "score",
    "org_scope",
    "state",
    "error_category",
    "event",
    "timestamp",
}
_PRIVATE_FIELD_NAMES = {
    "chatname": "chat_name",
    "message": "message",
    "reply": "reply",
    "context": "context",
}
_ALLOWED_AUDIT_VALUES = {
    "event": frozenset(
        {
            "managed-send-diagnostic",
            "managed-send",
            "nested-redaction",
            "rotation-check",
            "debug-check",
            "debug-disabled",
            "concurrency-check",
        }
    ),
    "state": frozenset(
        {
            "blocked",
            "vector_index_not_ready",
            "managed_preconditions_not_met",
            "api_failure",
            "latest_message_not_partner",
            "ledger_blocked",
            "ledger_turn_already_handled",
            "no_unreplied_message",
            "turn_already_handled",
            "send_already_pending",
            "no_candidate_selected",
            "send_queued",
            "preflight_checked",
            "preflight_rejected",
            "send_result",
        }
    ),
    "error_category": frozenset({"vector", "settings", "credentials", "memory", "io", "api", "preflight", "send"}),
    "org_scope": frozenset({"local", "municipal", "provincial", "national"}),
}
_SESSION_DEBUG = False
_LOGGER_INIT_LOCK = threading.RLock()


class RuntimeLoggerUnavailableError(RuntimeError):
    """The privacy-safe runtime logger cannot be initialized durably."""


def enable_session_debug(enabled: bool = True) -> None:
    """Enable privacy-safe diagnostic hashes for this process only."""
    global _SESSION_DEBUG
    _SESSION_DEBUG = bool(enabled)


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _normalized_path_key(value: object) -> str:
    return os.path.normcase(str(Path(value).resolve())).replace("\\", "/").casefold()


def _load_or_create_salt(log_dir: Path) -> bytes:
    salt_path = log_dir / ".runtime-salt"
    try:
        recover_atomic_write(salt_path)
        salt = salt_path.read_bytes()
    except FileNotFoundError:
        salt = b""
    except Exception as error:
        raise RuntimeLoggerUnavailableError("runtime log salt recovery failed") from error
    if salt:
        if len(salt) < 16:
            raise RuntimeLoggerUnavailableError("runtime log salt is invalid")
        return salt
    salt = secrets.token_bytes(32)
    try:
        atomic_write_bytes(salt_path, salt, valid_snapshots=[])
        try:
            os.chmod(salt_path, 0o600)
        except OSError:
            pass
        return salt
    except Exception as error:
        raise RuntimeLoggerUnavailableError("runtime log salt write failed") from error


def _private_hash(value: object, salt: bytes) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return "sha256:" + hmac.new(salt, payload, hashlib.sha256).hexdigest()


def _allowed_audit_value(key: str, value: object, *, salt: bytes | None) -> object | None:
    """Apply a positive schema; nested and unknown values are never serialized."""
    if key == "score":
        if type(value) in {int, float} and math.isfinite(float(value)) and 0 <= float(value) <= 100:
            return value
        return None
    if key == "timestamp":
        return None
    if key == "record_id":
        if type(value) is str and value and len(value) <= 512 and salt is not None:
            return _private_hash(value, salt)
        return None
    allowed_values = _ALLOWED_AUDIT_VALUES.get(key, frozenset())
    return value if type(value) is str and value in allowed_values else None


def redact_event(
    event: Mapping[str, object],
    *,
    salt: bytes | None = None,
    debug: bool | None = None,
) -> dict[str, object]:
    """Return only the non-content fields used for runtime auditing."""
    debug_enabled = _SESSION_DEBUG if debug is None else bool(debug)
    if debug_enabled:
        if salt is None:
            from app_paths import logs_dir

            log_dir = logs_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            salt = _load_or_create_salt(log_dir)
    redacted: dict[str, object] = {}
    for key in sorted(_AUDIT_FIELDS):
        if key not in event:
            continue
        allowed = _allowed_audit_value(key, event[key], salt=salt)
        if allowed is not None:
            redacted[key] = allowed
    if debug_enabled:
        for key, value in event.items():
            normalized = _normalized_key(key)
            canonical = _PRIVATE_FIELD_NAMES.get(normalized)
            if canonical is not None:
                redacted[canonical] = _private_hash(value, salt)
    return redacted


class _JsonLineFormatter(logging.Formatter):
    def __init__(self, salt: bytes) -> None:
        super().__init__()
        self.salt = salt

    def format(self, record: logging.LogRecord) -> str:
        event_data = getattr(record, "event_data", {})
        if not isinstance(event_data, Mapping):
            event_data = {}
        redacted = redact_event(event_data, salt=self.salt)
        redacted["timestamp"] = datetime.now(timezone.utc).isoformat()
        return json.dumps(redacted, ensure_ascii=False, sort_keys=True)


def build_runtime_logger(log_dir: Path) -> logging.Logger:
    resolved_dir = Path(log_dir).resolve()
    logger_key = hashlib.sha256(_normalized_path_key(resolved_dir).encode("utf-8")).hexdigest()
    logger = logging.getLogger(f"wechat_assistant.runtime.{logger_key}")
    log_path = resolved_dir / "runtime.log"
    log_path_key = _normalized_path_key(log_path)
    with _LOGGER_INIT_LOCK:
        try:
            resolved_dir.mkdir(parents=True, exist_ok=True)
            owned_handlers = [
                handler
                for handler in logger.handlers
                if getattr(handler, "_wechat_runtime_path", None)
                and _normalized_path_key(getattr(handler, "_wechat_runtime_path")) == log_path_key
            ]
            if not owned_handlers:
                salt = _load_or_create_salt(resolved_dir)
                handler = RotatingFileHandler(
                    log_path,
                    maxBytes=2_000_000,
                    backupCount=5,
                    encoding="utf-8",
                )
                handler._wechat_runtime_path = log_path_key
                handler.setFormatter(_JsonLineFormatter(salt))
                logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            logger.propagate = False
        except RuntimeLoggerUnavailableError:
            raise
        except Exception as error:
            raise RuntimeLoggerUnavailableError("runtime logger initialization failed") from error
    return logger
