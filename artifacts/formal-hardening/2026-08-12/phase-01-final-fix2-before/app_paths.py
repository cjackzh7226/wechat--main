from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "WeChatStyleReplyAssistant"
TEST_OVERRIDE = "WECHAT_ASSISTANT_DATA_DIR"


def data_dir() -> Path:
    override = os.environ.get(TEST_OVERRIDE, "").strip()
    if override:
        return Path(override).resolve()
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / APP_NAME
    return Path.home() / ".wechat_style_reply_assistant"


def config_path() -> Path:
    return data_dir() / "settings.json"


def memory_path() -> Path:
    return data_dir() / "memory.json"


def vector_path() -> Path:
    return data_dir() / "vector_memory.json"


def credential_path() -> Path:
    return data_dir() / "credentials.dat"


def ledger_path() -> Path:
    return data_dir() / "send_ledger.json"


def logs_dir() -> Path:
    return data_dir() / "logs"
