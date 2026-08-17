from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


APP_NAME = "WeChatStyleReplyAssistant"


def default_config_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "user_data" / "settings.json"
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME / "settings.json"
    return Path.home() / ".wechat_style_reply_assistant" / "settings.json"


@dataclass
class ChatArea:
    x_left: float = 0.31
    x_right: float = 0.98
    y_top: float = 0.09
    y_bottom: float = 0.91


@dataclass
class AppSettings:
    config_path: Path = field(default_factory=default_config_path)
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
    reply_source_mode: str = "model_memory"
    ocr_engine: str = "rapidocr"
    training_mode: bool = False
    auto_recognize: bool = False
    managed_auto_reply: bool = False
    enable_web_search: bool = True
    search_max_results: int = 3
    auto_reply_delay_seconds: float = 1.2
    poll_interval_ms: int = 800
    capture_cooldown_seconds: float = 0.8
    # 群聊：当前企业微信账号用户名（用于 @ 识别与回复 @ 回去）
    wecom_account_name: str = ""
    # 群聊 OCR 轮询间隔（比个人聊天快，@ 响应更及时）
    group_poll_interval_ms: int = 500
    chat_area: ChatArea = field(default_factory=ChatArea)

    def load(self) -> "AppSettings":
        if not self.config_path.exists():
            env_key = os.environ.get("TEXT_API_KEY", "") or os.environ.get("ZHIPU_API_KEY", "")
            if env_key:
                self.api_key = env_key
            return self

        data = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.api_provider = str(data.get("api_provider", self.api_provider))
        legacy_api_key = str(data.get("api_key", self.api_key))
        raw_provider_api_keys = data.get("provider_api_keys")
        if isinstance(raw_provider_api_keys, dict):
            self.provider_api_keys = {
                str(provider): str(api_key)
                for provider, api_key in raw_provider_api_keys.items()
                if provider is not None
            }
            # Keep older or manually edited files usable: the legacy field is
            # still the key for the currently selected provider.
            if self.api_provider not in self.provider_api_keys and legacy_api_key:
                self.provider_api_keys[self.api_provider] = legacy_api_key
            self.api_key = self.provider_api_keys.get(self.api_provider, "")
        else:
            # Migrate the original single-key format without losing the key.
            self.provider_api_keys = {}
            self.api_key = legacy_api_key
            if self.api_key:
                self.provider_api_keys[self.api_provider] = self.api_key
        self.base_url = str(data.get("base_url", self.base_url))
        self.model = str(data.get("model", self.model))
        self.vision_provider = str(data.get("vision_provider", self.vision_provider))
        self.vision_api_key = str(data.get("vision_api_key", self.vision_api_key))
        self.vision_base_url = str(data.get("vision_base_url", self.vision_base_url))
        self.vision_model = str(data.get("vision_model", self.vision_model))
        if self.api_provider == "zhipu" and self.model in {"glm-4.7-flash", "glm-4-flash"}:
            self.model = "glm-4-flash-250414"
        if self.api_provider == "deepseek" and self.model in {"deepseek-chat", "deepseek-reasoner"}:
            self.model = "deepseek-v4-flash"
        self.custom_headers = str(data.get("custom_headers", self.custom_headers))
        self.custom_body = str(data.get("custom_body", self.custom_body))
        self.custom_response_path = str(data.get("custom_response_path", self.custom_response_path))
        self.reply_source_mode = str(data.get("reply_source_mode", self.reply_source_mode))
        if self.reply_source_mode not in {"model", "memory", "model_memory"}:
            self.reply_source_mode = "model_memory"
        self.ocr_engine = str(data.get("ocr_engine", self.ocr_engine))
        self.training_mode = bool(data.get("training_mode", self.training_mode))
        self.auto_recognize = bool(data.get("auto_recognize", self.auto_recognize))
        self.managed_auto_reply = bool(data.get("managed_auto_reply", self.managed_auto_reply))
        self.enable_web_search = bool(data.get("enable_web_search", self.enable_web_search))
        self.search_max_results = max(1, min(6, int(data.get("search_max_results", self.search_max_results))))
        self.auto_reply_delay_seconds = float(data.get("auto_reply_delay_seconds", self.auto_reply_delay_seconds))
        stored_poll_interval = int(data.get("poll_interval_ms", self.poll_interval_ms))
        stored_capture_cooldown = float(
            data.get("capture_cooldown_seconds", self.capture_cooldown_seconds)
        )
        # 旧版本把 1200 ms / 4 s 写进了 user_data/settings.json。仅迁移
        # 这两个旧默认值，保留用户手工设置的其他轮询间隔。
        self.poll_interval_ms = 800 if stored_poll_interval == 1200 else stored_poll_interval
        self.capture_cooldown_seconds = (
            0.8 if stored_capture_cooldown == 4.0 else stored_capture_cooldown
        )
        self.wecom_account_name = str(data.get("wecom_account_name", self.wecom_account_name)).strip()
        stored_group_poll_interval = int(
            data.get("group_poll_interval_ms", self.group_poll_interval_ms)
        )
        self.group_poll_interval_ms = (
            500 if stored_group_poll_interval == 800 else stored_group_poll_interval
        )
        area = data.get("chat_area", {})
        if isinstance(area, dict):
            self.chat_area = ChatArea(
                x_left=float(area.get("x_left", self.chat_area.x_left)),
                x_right=float(area.get("x_right", self.chat_area.x_right)),
                y_top=float(area.get("y_top", self.chat_area.y_top)),
                y_bottom=float(area.get("y_bottom", self.chat_area.y_bottom)),
            )
            if self.chat_area.x_left < 0.25:
                self.chat_area.x_left = ChatArea().x_left
                self.chat_area.x_right = ChatArea().x_right
                self.chat_area.y_top = ChatArea().y_top
                self.chat_area.y_bottom = ChatArea().y_bottom
            if self.chat_area.y_bottom < 0.90:
                self.chat_area.y_bottom = ChatArea().y_bottom
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
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.provider_api_keys[self.api_provider] = self.api_key
        data = asdict(self)
        data["config_path"] = str(self.config_path)
        self.config_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
