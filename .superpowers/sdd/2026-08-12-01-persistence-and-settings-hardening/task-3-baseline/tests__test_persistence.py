import json
import sys

import pytest

import app_paths
from atomic_json import JsonDataError, atomic_write_json, load_json


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


def _require_examples_dict(value):
    if not isinstance(value, dict) or "examples" not in value:
        raise ValueError("examples must be a dictionary field")


def _require_numeric_config_version(value):
    if not isinstance(value, dict) or not isinstance(value.get("config_version"), int):
        raise ValueError("config_version must be an integer")


def test_load_recovers_newest_valid_backup(tmp_path):
    path = tmp_path / "memory.json"
    path.write_text("{broken", encoding="utf-8")
    (tmp_path / "memory.json.bak.1").write_text("{broken", encoding="utf-8")
    (tmp_path / "memory.json.bak.2").write_text('{"examples": [1]}', encoding="utf-8")
    (tmp_path / "memory.json.bak.3").write_text('{"examples": [2]}', encoding="utf-8")

    result = load_json(path, _require_examples_dict)

    assert result.data == {"examples": [1]}
    assert result.source == tmp_path / "memory.json.bak.2"
    assert result.recovered is True


def test_atomic_write_failed_validation_does_not_replace_existing_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"config_version": 2}', encoding="utf-8")

    with pytest.raises(ValueError):
        atomic_write_json(path, {"config_version": "bad"}, _require_numeric_config_version)

    assert json.loads(path.read_text(encoding="utf-8")) == {"config_version": 2}


def test_atomic_write_rotates_five_backups(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"config_version": 0}', encoding="utf-8")
    for index in range(1, 6):
        (tmp_path / f"settings.json.bak.{index}").write_text(
            json.dumps({"config_version": index}), encoding="utf-8"
        )

    atomic_write_json(path, {"config_version": 6}, _require_numeric_config_version)

    assert json.loads(path.read_text(encoding="utf-8")) == {"config_version": 6}
    assert [
        json.loads((tmp_path / f"settings.json.bak.{index}").read_text(encoding="utf-8"))
        for index in range(1, 6)
    ] == [{"config_version": value} for value in range(0, 5)]


def test_atomic_write_retains_older_valid_backup_when_newer_slots_are_corrupt(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"config_version": 0}', encoding="utf-8")
    (tmp_path / "settings.json.bak.1").write_text("{broken", encoding="utf-8")
    (tmp_path / "settings.json.bak.2").write_text('{"config_version": 2}', encoding="utf-8")
    (tmp_path / "settings.json.bak.3").write_text("{broken", encoding="utf-8")
    (tmp_path / "settings.json.bak.4").write_text("{broken", encoding="utf-8")
    (tmp_path / "settings.json.bak.5").write_text('{"config_version": 5}', encoding="utf-8")

    atomic_write_json(path, {"config_version": 6}, _require_numeric_config_version)

    assert [
        json.loads((tmp_path / f"settings.json.bak.{index}").read_text(encoding="utf-8"))
        for index in range(1, 4)
    ] == [
        {"config_version": 0},
        {"config_version": 2},
        {"config_version": 5},
    ]
    assert not (tmp_path / "settings.json.bak.4").exists()
    assert not (tmp_path / "settings.json.bak.5").exists()


def test_atomic_replace_failure_leaves_original_primary_readable(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text('{"config_version": 2}', encoding="utf-8")

    import atomic_json

    original_replace = atomic_json.os.replace

    def fail_primary_replace(source, destination):
        if destination == path:
            raise OSError("simulated replace failure")
        return original_replace(source, destination)

    monkeypatch.setattr(atomic_json.os, "replace", fail_primary_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        atomic_write_json(path, {"config_version": 3}, _require_numeric_config_version)

    assert json.loads(path.read_text(encoding="utf-8")) == {"config_version": 2}


def test_load_json_all_invalid_candidates_raises_without_creating_file(tmp_path):
    path = tmp_path / "memory.json"
    (tmp_path / "memory.json.bak.1").write_text("[]", encoding="utf-8")

    with pytest.raises(JsonDataError) as error:
        load_json(path, _require_examples_dict)

    assert not path.exists()
    assert "memory.json" in str(error.value)
