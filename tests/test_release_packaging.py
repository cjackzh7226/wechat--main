from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.audit_release_tree import audit_release_tree
from tools.prepare_release_seed import ENTERPRISE_KNOWLEDGE_SOURCE, prepare_release_seed


ROOT = Path(__file__).resolve().parents[1]
RELEASE_USER_DATA = ROOT / "release_seed" / "user_data"
FULL_INSTALLER = ROOT / "build" / "聊小智_installer.iss"


def _knowledge_item(index: int, source: str = ENTERPRISE_KNOWLEDGE_SOURCE) -> dict[str, str]:
    return {
        "question": f"合成问题 {index}",
        "cue": f"合成问题 {index}",
        "reply": f"合成答案 {index}",
        "source": source,
        "conversation_hash": f"hash-{index}",
    }


def test_prepare_release_seed_strips_keys_and_keeps_knowledge(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    examples = [_knowledge_item(index) for index in range(3)]
    (source / "memory.json").write_text(
        json.dumps({"style_summary": "合成", "examples": examples, "common_phrases": {}}),
        encoding="utf-8",
    )
    (source / "vector_memory.json").write_text(
        json.dumps(
            {
                "version": 1,
                "dim": 384,
                "items": [
                    {"conversation_hash": f"hash-{index}", "vector": {"1": 1.0}}
                    for index in range(3)
                ],
            }
        ),
        encoding="utf-8",
    )
    (source / "settings.json").write_text(
        json.dumps(
            {
                "api_provider": "synthetic",
                "api_key": "SYNTHETIC-TEXT-KEY-DO-NOT-SHIP",
                "provider_api_keys": {
                    "synthetic": "SYNTHETIC-TEXT-KEY-DO-NOT-SHIP"
                },
                "vision_api_key": "SYNTHETIC-VISION-KEY-DO-NOT-SHIP",
                "custom_headers": '{"Authorization":"Bearer SYNTHETIC-TEXT-KEY-DO-NOT-SHIP"}',
                "config_path": "C:/synthetic/private/settings.json",
                "model": "synthetic-model",
            }
        ),
        encoding="utf-8",
    )

    summary = prepare_release_seed(source, output, minimum_knowledge_count=3)

    sanitized = json.loads((output / "settings.json").read_text(encoding="utf-8"))
    serialized = (output / "settings.json").read_text(encoding="utf-8")
    assert summary.knowledge_count == 3
    assert sanitized["api_key"] == ""
    assert sanitized["provider_api_keys"] == {}
    assert sanitized["vision_api_key"] == ""
    assert "config_path" not in sanitized
    assert "SYNTHETIC-TEXT-KEY-DO-NOT-SHIP" not in serialized
    assert "SYNTHETIC-VISION-KEY-DO-NOT-SHIP" not in serialized
    assert sanitized["model"] == "synthetic-model"


def test_prepare_release_seed_rejects_non_knowledge_chat_data(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    examples = [_knowledge_item(1), _knowledge_item(2, source="observed_chat")]
    (source / "memory.json").write_text(
        json.dumps({"examples": examples}), encoding="utf-8"
    )
    (source / "vector_memory.json").write_text(
        json.dumps(
            {
                "items": [
                    {"conversation_hash": "hash-1", "vector": {"1": 1.0}},
                    {"conversation_hash": "hash-2", "vector": {"1": 1.0}},
                ]
            }
        ),
        encoding="utf-8",
    )
    (source / "settings.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="非企业年报知识记录"):
        prepare_release_seed(source, tmp_path / "output", minimum_knowledge_count=2)


def test_release_tree_audit_detects_exact_and_recognizable_key_fragments(
    tmp_path: Path,
) -> None:
    source_settings = tmp_path / "settings.json"
    release_tree = tmp_path / "release"
    release_tree.mkdir()
    source_settings.write_text(
        json.dumps({"api_key": "SYNTHETIC-KEY-1234567890-DO-NOT-SHIP"}),
        encoding="utf-8",
    )
    (release_tree / "safe.txt").write_text("公开合成内容", encoding="utf-8")
    assert audit_release_tree(source_settings, release_tree)["secret_match_count"] == 0

    (release_tree / "unsafe.txt").write_text(
        "SYNTHETIC-KE", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="secret_match_count=1"):
        audit_release_tree(source_settings, release_tree)


def test_checked_in_release_seed_contains_full_knowledge_and_no_keys() -> None:
    memory = json.loads((RELEASE_USER_DATA / "memory.json").read_text(encoding="utf-8"))
    vector = json.loads(
        (RELEASE_USER_DATA / "vector_memory.json").read_text(encoding="utf-8")
    )
    settings_text = (RELEASE_USER_DATA / "settings.json").read_text(encoding="utf-8")
    settings = json.loads(settings_text)
    examples = memory["examples"]

    assert len(examples) >= 400
    assert len(vector["items"]) == len(examples)
    assert all(item.get("source") == ENTERPRISE_KNOWLEDGE_SOURCE for item in examples)
    assert settings.get("api_key") == ""
    assert settings.get("provider_api_keys") == {}
    assert settings.get("vision_api_key") == ""
    assert "credentials.dat" not in {path.name for path in RELEASE_USER_DATA.iterdir()}


def test_full_installer_is_not_update_package_and_preserves_existing_user_data() -> None:
    script = FULL_INSTALLER.read_text(encoding="utf-8-sig")

    assert "完整安装包" in script
    assert "更新包" not in script
    assert '#define InstallerAppId "{{D67B9715-251B-4BBE-B2AB-3481CC740C70}"' in script
    assert "ShouldInstallSeedKnowledge" in script
    assert "ShouldInstallSeedSettings" in script
    assert 'user_data\\settings.json"' in script
    assert 'user_data\\memory.json"' in script
    assert 'user_data\\vector_memory.json"' in script
    assert "uninsneveruninstall" in script
    assert "credentials.dat" not in script
