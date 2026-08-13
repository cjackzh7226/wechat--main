import sys

import app_paths


def test_source_and_frozen_use_same_explicit_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("WECHAT_ASSISTANT_DATA_DIR", str(tmp_path))
    assert app_paths.config_path() == tmp_path / "settings.json"
    assert app_paths.memory_path() == tmp_path / "memory.json"
    assert app_paths.vector_path() == tmp_path / "vector_memory.json"


def test_default_root_is_appdata_even_when_frozen(tmp_path, monkeypatch):
    monkeypatch.delenv("WECHAT_ASSISTANT_DATA_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert app_paths.data_dir() == tmp_path / "WeChatStyleReplyAssistant"
