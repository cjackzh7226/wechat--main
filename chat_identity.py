"""Stable, privacy-preserving identity for managed WeCom observations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
import unicodedata
from typing import Literal, Sequence, TypeAlias


EligibleChatType: TypeAlias = Literal["personal", "group"]
_ELIGIBLE_CHAT_TYPES = {"personal", "group"}


def normalize_chat_title(text: str) -> str:
    if type(text) is not str:
        raise TypeError("chat title must be a string")
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def _normalize_turn_text(text: str) -> str:
    if type(text) is not str:
        raise TypeError("turn message must be a string")
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChatIdentity:
    hwnd: int
    top_title: str
    chat_type: EligibleChatType

    def __post_init__(self) -> None:
        if type(self.hwnd) is not int or self.hwnd <= 0:
            raise ValueError("hwnd must be a positive integer")
        if type(self.chat_type) is not str or self.chat_type not in _ELIGIBLE_CHAT_TYPES:
            raise ValueError("chat_type must be personal or group")
        normalized = normalize_chat_title(self.top_title)
        if not normalized:
            raise ValueError("top_title must not be empty")
        object.__setattr__(self, "top_title", normalized)

    @property
    def key(self) -> str:
        material = "\n".join(("wecom", str(self.hwnd), self.top_title, self.chat_type))
        return _sha256(material)


def turn_fingerprint(
    identity: ChatIdentity,
    messages: Sequence[str],
    *,
    sender_name: str = "",
) -> str:
    if not isinstance(identity, ChatIdentity):
        raise TypeError("identity must be ChatIdentity")
    if isinstance(messages, (str, bytes)) or not isinstance(messages, Sequence):
        raise TypeError("messages must be a sequence of strings")
    normalized_messages = tuple(_normalize_turn_text(message) for message in messages)
    if not normalized_messages or any(not message for message in normalized_messages):
        raise ValueError("messages must contain non-empty strings")
    if type(sender_name) is not str:
        raise TypeError("sender_name must be a string")
    normalized_sender = normalize_chat_title(sender_name).casefold() if identity.chat_type == "group" else ""
    material = "\x1f".join(
        (identity.key, identity.chat_type, normalized_sender, "\x1e".join(normalized_messages))
    )
    return _sha256(material)


@dataclass(frozen=True)
class ChatObservation:
    identity: ChatIdentity
    context: str
    customer_turn: tuple[str, ...]
    sender_name: str = ""
    captured_at: float = 0.0
    foreground: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ChatIdentity):
            raise TypeError("identity must be ChatIdentity")
        if type(self.context) is not str:
            raise TypeError("context must be a string")
        if type(self.customer_turn) is not tuple:
            raise TypeError("customer_turn must be a tuple")
        if not self.customer_turn or any(type(item) is not str or not item.strip() for item in self.customer_turn):
            raise ValueError("customer_turn must contain non-empty strings")
        if type(self.sender_name) is not str:
            raise TypeError("sender_name must be a string")
        if self.identity.chat_type == "group" and not normalize_chat_title(self.sender_name):
            raise ValueError("group observation requires sender_name")
        if type(self.captured_at) not in {int, float} or not math.isfinite(float(self.captured_at)):
            raise ValueError("captured_at must be finite")
        if type(self.foreground) is not bool:
            raise TypeError("foreground must be a boolean")

    @property
    def turn_key(self) -> str:
        return turn_fingerprint(
            self.identity,
            self.customer_turn,
            sender_name=self.sender_name,
        )

    @property
    def context_hash(self) -> str:
        return _sha256(unicodedata.normalize("NFKC", self.context))


class StableIdentityTracker:
    def __init__(self, required_seconds: float = 1.2) -> None:
        if type(required_seconds) not in {int, float} or not math.isfinite(float(required_seconds)):
            raise ValueError("required_seconds must be finite")
        if float(required_seconds) < 0:
            raise ValueError("required_seconds must not be negative")
        self.required_seconds = float(required_seconds)
        self._identity: ChatIdentity | None = None
        self._context_hash = ""
        self._first_seen_at = 0.0
        self._samples = 0

    def reset(self) -> None:
        self._identity = None
        self._context_hash = ""
        self._first_seen_at = 0.0
        self._samples = 0

    def observe(self, identity: ChatIdentity, context_hash: str, *, now: float) -> bool:
        if not isinstance(identity, ChatIdentity):
            raise TypeError("identity must be ChatIdentity")
        if type(context_hash) is not str or not context_hash:
            raise ValueError("context_hash must be a non-empty string")
        if type(now) not in {int, float} or not math.isfinite(float(now)):
            raise ValueError("now must be finite")
        observed_at = float(now)
        changed = (
            self._identity != identity
            or self._context_hash != context_hash
            or observed_at < self._first_seen_at
        )
        if changed:
            self._identity = identity
            self._context_hash = context_hash
            self._first_seen_at = observed_at
            self._samples = 1
            return False
        self._samples += 1
        return bool(
            self._samples >= 2
            and observed_at - self._first_seen_at + 1e-9 >= self.required_seconds
        )

