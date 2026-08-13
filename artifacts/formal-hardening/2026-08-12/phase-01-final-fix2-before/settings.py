from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from app_paths import config_path, credential_path
from atomic_json import JsonDataError, atomic_write_json, load_json
from secure_store import CredentialStore


CONFIG_VERSION = 2
MANAGED_MODES = {"off", "observe", "controlled", "full"}
_VISION_PREFIX = "vision:"
_FORBIDDEN_SETTINGS_KEY_FIELDS = ("api_key", "vision_api_key", "provider_api_keys")
_PERSISTED_STRING_FIELDS = (
    "api_provider",
    "base_url",
    "model",
    "vision_provider",
    "vision_base_url",
    "vision_model",
    "custom_headers",
    "custom_body",
    "custom_response_path",
    "ocr_engine",
    "wecom_account_name",
)
_API_KEY_SENTINEL = "__WECHAT_ASSISTANT_API_KEY_SENTINEL__"
_APPROVED_CREDENTIAL_VALUES = {
    _API_KEY_SENTINEL,
    f"Bearer {_API_KEY_SENTINEL}",
    f"Basic {_API_KEY_SENTINEL}",
}
_CREDENTIAL_SCHEME = re.compile(r"(?i)\b(?:bearer|basic)\s+(\S+)")
_SENSITIVE_KEY_PARTS = {
    "auth",
    "authorization",
    "authentication",
    "oauth",
    "key",
    "token",
    "secret",
    "password",
    "credential",
}
_SENSITIVE_COMPACT_KEYS = {
    "apikey",
    "accesstoken",
    "clientsecret",
    "proxyauthorization",
    "xauth",
}


class SettingsValidationError(ValueError):
    """Raised when settings do not match the formal versioned schema."""


class SettingsPersistenceError(RuntimeError):
    """Raised when settings or credential bytes cannot be safely read or written."""


def default_config_path() -> Path:
    return config_path()


def require_bool(data: dict[str, object], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if type(value) is not bool:
        raise SettingsValidationError(f"{key} must be a boolean")
    return value


def bounded_number(value: object, key: str, low: float, high: float) -> float:
    if type(value) not in {int, float}:
        raise SettingsValidationError(f"{key} must be a number")
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise SettingsValidationError(f"{key} must be in range {low}..{high}")
    return number


def _bounded_integer(value: object, key: str, low: int, high: int) -> int:
    bounded_number(value, key, low, high)
    if type(value) is not int:
        raise SettingsValidationError(f"{key} must be an integer")
    return value


def _string_value(data: dict[str, object], key: str, default: str) -> str:
    value = data.get(key, default)
    if type(value) is not str:
        raise SettingsValidationError(f"{key} must be a string")
    return value


def _validate_credential_template(value: str, key: str) -> None:
    rendered = value.replace("{api_key}", _API_KEY_SENTINEL)
    rendered = rendered.replace("{messages_json}", "[]")
    for placeholder, sentinel in (
        ("{model}", "MODEL_SENTINEL"),
        ("{base_url}", "BASE_URL_SENTINEL"),
        ("{prompt}", "PROMPT_SENTINEL"),
    ):
        rendered = rendered.replace(placeholder, sentinel)
    try:
        document = json.loads(rendered)
    except (TypeError, ValueError) as error:
        raise SettingsValidationError(f"{key} must be a valid JSON template") from error
    if type(document) is not dict:
        raise SettingsValidationError(f"{key} must render to a JSON object")
    _validate_decoded_credential_values(document, key)


def _validate_decoded_credential_values(value: object, template_key: str) -> None:
    if type(value) is dict:
        for field_name, nested in value.items():
            if type(field_name) is not str:
                raise SettingsValidationError(f"{template_key} JSON keys must be strings")
            if _is_sensitive_credential_key(field_name):
                if type(nested) is not str or nested.strip() not in _APPROVED_CREDENTIAL_VALUES:
                    raise SettingsValidationError(f"{template_key} contains a literal credential")
            else:
                _validate_decoded_credential_values(nested, template_key)
        return
    if type(value) is list:
        for nested in value:
            _validate_decoded_credential_values(nested, template_key)
        return
    if type(value) is str:
        for match in _CREDENTIAL_SCHEME.finditer(value):
            if match.group(1) != _API_KEY_SENTINEL:
                raise SettingsValidationError(f"{template_key} contains a literal credential")


def _is_sensitive_credential_key(field_name: str) -> bool:
    camel_split = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", field_name)
    parts = re.findall(r"[a-z0-9]+", camel_split.casefold())
    compact = "".join(parts)
    return bool(_SENSITIVE_KEY_PARTS.intersection(parts) or compact in _SENSITIVE_COMPACT_KEYS)


@dataclass
class ChatArea:
    x_left: float = 0.31
    x_right: float = 0.98
    y_top: float = 0.09
    y_bottom: float = 0.91

    def validate(self) -> None:
        for key in ("x_left", "x_right", "y_top", "y_bottom"):
            bounded_number(getattr(self, key), f"chat_area.{key}", 0.0, 1.0)
        if self.x_left >= self.x_right:
            raise SettingsValidationError("chat_area requires x_left < x_right")
        if self.y_top >= self.y_bottom:
            raise SettingsValidationError("chat_area requires y_top < y_bottom")


@dataclass
class AppSettings:
    PERSISTED_FIELDS: ClassVar[tuple[str, ...]] = (
        "config_version",
        "api_provider",
        "base_url",
        "model",
        "vision_provider",
        "vision_base_url",
        "vision_model",
        "custom_headers",
        "custom_body",
        "custom_response_path",
        "reply_source_mode",
        "ocr_engine",
        "training_mode",
        "auto_recognize",
        "managed_mode",
        "managed_allowed_chats",
        "enable_web_search",
        "search_max_results",
        "auto_reply_delay_seconds",
        "poll_interval_ms",
        "capture_cooldown_seconds",
        "wecom_account_name",
        "group_poll_interval_ms",
        "chat_area",
    )

    config_path: Path = field(default_factory=default_config_path)
    credential_store: object | None = field(default=None, repr=False)
    config_version: int = CONFIG_VERSION
    api_provider: str = "zhipu"
    api_key: str = ""
    provider_api_keys: dict[str, str] = field(default_factory=dict)
    base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    model: str = "glm-4-flash-250414"
    vision_provider: str = "zhipu"
    vision_api_key: str = ""
    vision_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    vision_model: str = "glm-4v-flash"
    custom_headers: str = '{"Authorization":"Bearer {api_key}","Content-Type":"application/json"}'
    custom_body: str = '{"model":"{model}","messages":{messages_json}}'
    custom_response_path: str = "choices.0.message.content"
    reply_source_mode: str = "knowledge_only"
    ocr_engine: str = "rapidocr"
    training_mode: bool = False
    auto_recognize: bool = False
    managed_mode: str = "off"
    managed_allowed_chats: list[str] = field(default_factory=list)
    enable_web_search: bool = False
    search_max_results: int = 3
    auto_reply_delay_seconds: float = 1.2
    poll_interval_ms: int = 1200
    capture_cooldown_seconds: float = 4.0
    wecom_account_name: str = ""
    group_poll_interval_ms: int = 800
    chat_area: ChatArea = field(default_factory=ChatArea)

    def __post_init__(self) -> None:
        self.config_path = Path(self.config_path)
        if self.credential_store is None:
            default_path = default_config_path()
            store_path = credential_path() if self.config_path == default_path else self.config_path.with_name("credentials.dat")
            self.credential_store = CredentialStore(path=store_path)

    @property
    def managed_auto_reply(self) -> bool:
        """Compatibility gate: only explicit full mode enables the legacy sender."""
        return self.managed_mode == "full"

    @managed_auto_reply.setter
    def managed_auto_reply(self, value: bool) -> None:
        if type(value) is not bool:
            raise SettingsValidationError("managed_auto_reply must be a boolean")
        self.managed_mode = "full" if value else "off"

    def validate(self) -> None:
        if type(self.config_version) is not int or self.config_version != CONFIG_VERSION:
            raise SettingsValidationError(f"config_version must be {CONFIG_VERSION}")
        for key in _PERSISTED_STRING_FIELDS:
            if type(getattr(self, key)) is not str:
                raise SettingsValidationError(f"{key} must be a string")
        _validate_credential_template(self.custom_headers, "custom_headers")
        _validate_credential_template(self.custom_body, "custom_body")
        for key in ("training_mode", "auto_recognize", "enable_web_search"):
            if type(getattr(self, key)) is not bool:
                raise SettingsValidationError(f"{key} must be a boolean")
        if self.reply_source_mode != "knowledge_only":
            raise SettingsValidationError("reply_source_mode must be knowledge_only")
        if self.enable_web_search is not False:
            raise SettingsValidationError("enable_web_search must be false")
        if type(self.managed_mode) is not str or self.managed_mode not in MANAGED_MODES:
            raise SettingsValidationError("managed_mode is invalid")
        if type(self.managed_allowed_chats) is not list or any(
            type(chat) is not str or not chat.strip() for chat in self.managed_allowed_chats
        ):
            raise SettingsValidationError("managed_allowed_chats must be a list of non-empty strings")
        if len(set(self.managed_allowed_chats)) != len(self.managed_allowed_chats):
            raise SettingsValidationError("managed_allowed_chats must not contain duplicates")
        self.search_max_results = _bounded_integer(self.search_max_results, "search_max_results", 1, 6)
        self.poll_interval_ms = _bounded_integer(self.poll_interval_ms, "poll_interval_ms", 250, 10000)
        self.group_poll_interval_ms = _bounded_integer(
            self.group_poll_interval_ms,
            "group_poll_interval_ms",
            250,
            10000,
        )
        self.auto_reply_delay_seconds = bounded_number(
            self.auto_reply_delay_seconds,
            "auto_reply_delay_seconds",
            0.5,
            10,
        )
        self.capture_cooldown_seconds = bounded_number(
            self.capture_cooldown_seconds,
            "capture_cooldown_seconds",
            0.5,
            60,
        )
        if not isinstance(self.chat_area, ChatArea):
            raise SettingsValidationError("chat_area must be a ChatArea")
        self.chat_area.validate()

    def _validate_raw_document(self, data: object) -> None:
        if not isinstance(data, dict):
            raise SettingsValidationError("settings root must be an object")
        version = data.get("config_version", 1)
        if type(version) is not int or version not in {1, CONFIG_VERSION}:
            raise SettingsValidationError("unsupported config_version")
        for key in _PERSISTED_STRING_FIELDS:
            value = _string_value(data, key, getattr(self, key))
            if key in {"custom_headers", "custom_body"}:
                _validate_credential_template(value, key)
        if version == CONFIG_VERSION:
            for forbidden in _FORBIDDEN_SETTINGS_KEY_FIELDS:
                if forbidden in data:
                    raise SettingsValidationError(f"{forbidden} is forbidden in version 2 settings")
            if "managed_auto_reply" in data:
                require_bool(data, "managed_auto_reply", False)
                raise SettingsValidationError("managed_auto_reply is obsolete in version 2 settings")
        else:
            for key in ("api_key", "vision_api_key"):
                if type(data.get(key, "")) is not str:
                    raise SettingsValidationError(f"{key} must be a string")
            raw_provider_keys = data.get("provider_api_keys", {})
            if type(raw_provider_keys) is not dict or any(
                type(provider) is not str or type(secret) is not str
                for provider, secret in raw_provider_keys.items()
            ):
                raise SettingsValidationError("provider_api_keys must map strings to strings")
        for key in ("training_mode", "auto_recognize"):
            require_bool(data, key, getattr(self, key))
        if version == 1:
            require_bool(data, "managed_auto_reply", False)
        require_bool(data, "enable_web_search", False)
        for key, low, high in (
            ("search_max_results", 1, 6),
            ("poll_interval_ms", 250, 10000),
            ("group_poll_interval_ms", 250, 10000),
        ):
            _bounded_integer(data.get(key, getattr(self, key)), key, low, high)
        for key, low, high in (
            ("auto_reply_delay_seconds", 0.5, 10),
            ("capture_cooldown_seconds", 0.5, 60),
        ):
            bounded_number(data.get(key, getattr(self, key)), key, low, high)
        area = data.get("chat_area")
        if area is not None:
            if not isinstance(area, dict):
                raise SettingsValidationError("chat_area must be an object")
            values = {}
            for key in ("x_left", "x_right", "y_top", "y_bottom"):
                values[key] = bounded_number(area.get(key, getattr(self.chat_area, key)), f"chat_area.{key}", 0, 1)
            ChatArea(**values).validate()
        if version == CONFIG_VERSION:
            mode = data.get("managed_mode", "off")
            if type(mode) is not str or mode not in MANAGED_MODES:
                raise SettingsValidationError("managed_mode is invalid")
            allowed = data.get("managed_allowed_chats", [])
            if type(allowed) is not list or any(type(chat) is not str or not chat.strip() for chat in allowed):
                raise SettingsValidationError("managed_allowed_chats must be a list of non-empty strings")
            if len(set(allowed)) != len(allowed):
                raise SettingsValidationError("managed_allowed_chats must not contain duplicates")
            if data.get("reply_source_mode", "knowledge_only") != "knowledge_only":
                raise SettingsValidationError("reply_source_mode must be knowledge_only")
            if data.get("enable_web_search", False) is not False:
                raise SettingsValidationError("enable_web_search must be false")

    def _validate_redacted_document(self, data: object) -> None:
        if not isinstance(data, dict):
            raise SettingsValidationError("settings root must be an object")
        for forbidden in _FORBIDDEN_SETTINGS_KEY_FIELDS:
            if forbidden in data:
                raise SettingsValidationError(f"{forbidden} is forbidden in persisted settings")
        self._validate_raw_document(data)

    def _load_document(self) -> dict[str, object]:
        try:
            return load_json(self.config_path, self._validate_raw_document).data
        except JsonDataError:
            try:
                value = json.loads(self.config_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError) as error:
                raise SettingsPersistenceError("settings file could not be read") from error
            except json.JSONDecodeError:
                raise
            self._validate_raw_document(value)
            raise
        except (OSError, UnicodeDecodeError) as error:
            raise SettingsPersistenceError("settings file could not be read") from error

    def _load_credentials(self) -> dict[str, str]:
        try:
            return self.credential_store.load()
        except Exception as error:
            self.managed_mode = "off"
            raise SettingsPersistenceError("credential store could not be read") from error

    def _save_credentials(self, credentials: dict[str, str]) -> None:
        try:
            self.credential_store.save(credentials)
        except Exception as error:
            self.managed_mode = "off"
            raise SettingsPersistenceError("credential store could not be written") from error

    def _apply_document(self, data: dict[str, object], *, legacy: bool) -> None:
        for key in (
            "api_provider",
            "base_url",
            "model",
            "vision_provider",
            "vision_base_url",
            "vision_model",
            "custom_headers",
            "custom_body",
            "custom_response_path",
            "ocr_engine",
            "wecom_account_name",
        ):
            setattr(self, key, _string_value(data, key, getattr(self, key)))
        self.training_mode = require_bool(data, "training_mode", self.training_mode)
        self.auto_recognize = require_bool(data, "auto_recognize", self.auto_recognize)
        self.enable_web_search = False
        self.reply_source_mode = "knowledge_only"
        self.search_max_results = _bounded_integer(
            data.get("search_max_results", self.search_max_results),
            "search_max_results",
            1,
            6,
        )
        self.poll_interval_ms = _bounded_integer(
            data.get("poll_interval_ms", self.poll_interval_ms),
            "poll_interval_ms",
            250,
            10000,
        )
        self.group_poll_interval_ms = _bounded_integer(
            data.get("group_poll_interval_ms", self.group_poll_interval_ms),
            "group_poll_interval_ms",
            250,
            10000,
        )
        self.auto_reply_delay_seconds = bounded_number(
            data.get("auto_reply_delay_seconds", self.auto_reply_delay_seconds),
            "auto_reply_delay_seconds",
            0.5,
            10,
        )
        self.capture_cooldown_seconds = bounded_number(
            data.get("capture_cooldown_seconds", self.capture_cooldown_seconds),
            "capture_cooldown_seconds",
            0.5,
            60,
        )
        area = data.get("chat_area")
        if isinstance(area, dict):
            self.chat_area = ChatArea(
                **{
                    key: bounded_number(area.get(key, getattr(self.chat_area, key)), f"chat_area.{key}", 0, 1)
                    for key in ("x_left", "x_right", "y_top", "y_bottom")
                }
            )
        if legacy:
            self.managed_mode = "observe" if require_bool(data, "managed_auto_reply", False) else "off"
            self.managed_allowed_chats = []
        else:
            self.managed_mode = _string_value(data, "managed_mode", "off")
            self.managed_allowed_chats = list(data.get("managed_allowed_chats", []))
        if self.api_provider == "zhipu" and self.model in {"glm-4.7-flash", "glm-4-flash"}:
            self.model = "glm-4-flash-250414"
        if self.api_provider == "deepseek" and self.model in {"deepseek-chat", "deepseek-reasoner"}:
            self.model = "deepseek-v4-flash"
        self.config_version = CONFIG_VERSION
        self.validate()

    def _apply_credentials(self, credentials: dict[str, str]) -> None:
        self.provider_api_keys = {
            provider: secret
            for provider, secret in credentials.items()
            if not provider.startswith(_VISION_PREFIX)
        }
        self.api_key = self.provider_api_keys.get(self.api_provider, "")
        self.vision_api_key = credentials.get(f"{_VISION_PREFIX}{self.vision_provider}", "")

    def _runtime_credentials(self) -> dict[str, str]:
        values = {
            str(provider): str(secret)
            for provider, secret in self.provider_api_keys.items()
            if provider and secret
        }
        if self.api_provider and self.api_key:
            values[self.api_provider] = self.api_key
        if self.vision_provider and self.vision_api_key:
            values[f"{_VISION_PREFIX}{self.vision_provider}"] = self.vision_api_key
        return values

    def _legacy_credentials(self, data: dict[str, object]) -> dict[str, str]:
        values: dict[str, str] = {}
        raw_provider_keys = data.get("provider_api_keys", {})
        if raw_provider_keys is not None and not isinstance(raw_provider_keys, dict):
            raise SettingsValidationError("provider_api_keys must be an object")
        if isinstance(raw_provider_keys, dict):
            for provider, secret in raw_provider_keys.items():
                if type(provider) is not str or type(secret) is not str:
                    raise SettingsValidationError("provider_api_keys entries must be strings")
                if provider and secret:
                    values[provider] = secret
        legacy_api_key = data.get("api_key", "")
        legacy_vision_key = data.get("vision_api_key", "")
        if type(legacy_api_key) is not str or type(legacy_vision_key) is not str:
            raise SettingsValidationError("legacy API keys must be strings")
        if legacy_api_key:
            values[self.api_provider] = legacy_api_key
        if legacy_vision_key:
            values[f"{_VISION_PREFIX}{self.vision_provider}"] = legacy_vision_key
        return values

    def _settings_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {}
        for key in self.PERSISTED_FIELDS:
            value = getattr(self, key)
            if key == "chat_area":
                value = {
                    "x_left": self.chat_area.x_left,
                    "x_right": self.chat_area.x_right,
                    "y_top": self.chat_area.y_top,
                    "y_bottom": self.chat_area.y_bottom,
                }
            elif key == "managed_allowed_chats":
                value = list(self.managed_allowed_chats)
            payload[key] = value
        return payload

    def _persist_settings(self) -> None:
        self.validate()
        try:
            atomic_write_json(
                self.config_path,
                self._settings_payload(),
                self._validate_redacted_document,
                backup_count=5,
            )
        except OSError as error:
            self.managed_mode = "off"
            raise SettingsPersistenceError("settings file could not be written") from error

    def load(self) -> "AppSettings":
        if not self.config_path.exists():
            credentials = self._load_credentials()
            self._apply_credentials(credentials)
            env_key = os.environ.get("TEXT_API_KEY", "") or os.environ.get("ZHIPU_API_KEY", "")
            if env_key and not self.api_key:
                self.api_key = env_key
                self.provider_api_keys[self.api_provider] = env_key
            return self

        data = self._load_document()
        legacy = data.get("config_version", 1) == 1
        self._apply_document(data, legacy=legacy)
        if not legacy:
            self._apply_credentials(self._load_credentials())
            return self

        encrypted = self._load_credentials()
        plaintext = self._legacy_credentials(data)
        encrypted.update(plaintext)
        self._apply_credentials(encrypted)
        if any(field in data for field in ("api_key", "vision_api_key", "provider_api_keys")):
            self._save_credentials(encrypted)
            read_back = self._load_credentials()
            if read_back != encrypted:
                raise SettingsPersistenceError("credential read-back did not match encrypted write")
            self._apply_credentials(read_back)
        self._persist_settings()
        return self

    def get_provider_api_key(self, provider: str) -> str:
        provider = str(provider or "")
        if provider in self.provider_api_keys:
            return self.provider_api_keys[provider]
        if provider == self.api_provider:
            return self.api_key
        return ""

    def set_provider_api_key(self, provider: str, api_key: str) -> None:
        provider = str(provider or "")
        if not provider:
            return
        value = str(api_key or "")
        self.provider_api_keys[provider] = value
        if provider == self.api_provider:
            self.api_key = value

    def save(self) -> None:
        self.validate()
        credentials = self._runtime_credentials()
        self._save_credentials(credentials)
        read_back = self._load_credentials()
        if read_back != credentials:
            self.managed_mode = "off"
            raise SettingsPersistenceError("credential read-back did not match encrypted write")
        self._apply_credentials(read_back)
        self._persist_settings()
