from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ENTERPRISE_KNOWLEDGE_SOURCE = "enterprise_annual_report_kb"
SAFE_CUSTOM_HEADERS = (
    '{"Authorization":"Bearer {api_key}","Content-Type":"application/json"}'
)
SECRET_FIELD_NAMES = {"api_key", "provider_api_keys", "vision_api_key"}


@dataclass(frozen=True)
class ReleaseSeedSummary:
    knowledge_count: int
    vector_item_count: int
    memory_sha256: str
    vector_sha256: str
    settings_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "knowledge_count": self.knowledge_count,
            "vector_item_count": self.vector_item_count,
            "memory_sha256": self.memory_sha256,
            "vector_sha256": self.vector_sha256,
            "settings_sha256": self.settings_sha256,
            "secret_value_match_count": 0,
        }


def prepare_release_seed(
    source_user_data: Path,
    output_user_data: Path,
    *,
    minimum_knowledge_count: int = 400,
) -> ReleaseSeedSummary:
    """Create a publishable user_data seed without exposing credential values.

    Only the enterprise annual-report knowledge library is accepted. This keeps
    observed chats and manually learned replies out of source control and public
    installers even when they happen to coexist in a local memory.json.
    """

    source_user_data = source_user_data.resolve(strict=True)
    memory_path = source_user_data / "memory.json"
    vector_path = source_user_data / "vector_memory.json"
    settings_path = source_user_data / "settings.json"

    memory = _load_object(memory_path)
    vector = _load_object(vector_path)
    settings = _load_object(settings_path)

    examples = memory.get("examples")
    if not isinstance(examples, list):
        raise ValueError("memory.json 的 examples 不是列表")
    if len(examples) < minimum_knowledge_count:
        raise ValueError(
            f"知识记录仅 {len(examples)} 条，低于要求的 {minimum_knowledge_count} 条"
        )
    for index, item in enumerate(examples):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index + 1} 条知识记录不是对象")
        if item.get("source") != ENTERPRISE_KNOWLEDGE_SOURCE:
            raise ValueError("检测到非企业年报知识记录，拒绝生成公开发布种子")
        if not str(item.get("question", "")).strip() or not str(item.get("reply", "")).strip():
            raise ValueError(f"第 {index + 1} 条知识记录缺少问题或答案")

    vector_items = vector.get("items")
    if not isinstance(vector_items, list) or not vector_items:
        raise ValueError("vector_memory.json 缺少有效 items")
    if len(vector_items) != len(examples):
        raise ValueError(
            f"向量记录数 {len(vector_items)} 与知识记录数 {len(examples)} 不一致"
        )

    secret_values = _collect_secret_values(settings)
    sanitized_settings = _sanitize_settings(settings)
    settings_bytes = _json_bytes(sanitized_settings)
    _assert_secret_values_absent(settings_bytes, secret_values)

    memory_bytes = _json_bytes(memory)
    vector_bytes = _json_bytes(vector)
    output_user_data.mkdir(parents=True, exist_ok=True)
    _atomic_write(output_user_data / "memory.json", memory_bytes)
    _atomic_write(output_user_data / "vector_memory.json", vector_bytes)
    _atomic_write(output_user_data / "settings.json", settings_bytes)

    for forbidden_name in ("credentials.dat", "send_ledger.json"):
        forbidden = output_user_data / forbidden_name
        if forbidden.exists():
            raise ValueError(f"发布种子中出现禁止文件：{forbidden_name}")

    return ReleaseSeedSummary(
        knowledge_count=len(examples),
        vector_item_count=len(vector_items),
        memory_sha256=_sha256_bytes(memory_bytes),
        vector_sha256=_sha256_bytes(vector_bytes),
        settings_sha256=_sha256_bytes(settings_bytes),
    )


def _load_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name} 的顶层结构不是对象")
    return loaded


def _collect_secret_values(settings: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for field_name in ("api_key", "vision_api_key"):
        value = settings.get(field_name)
        if isinstance(value, str) and value:
            values.add(value)
    provider_keys = settings.get("provider_api_keys")
    if isinstance(provider_keys, dict):
        for value in provider_keys.values():
            if isinstance(value, str) and value:
                values.add(value)

    raw_headers = settings.get("custom_headers")
    if isinstance(raw_headers, str):
        try:
            headers = json.loads(raw_headers)
        except json.JSONDecodeError:
            headers = None
        if isinstance(headers, dict):
            for name, value in headers.items():
                normalized_name = str(name).casefold().replace("-", "_")
                if any(token in normalized_name for token in ("authorization", "api_key", "token")):
                    text = str(value)
                    if text and "{api_key}" not in text:
                        values.add(text)
    return values


def _sanitize_settings(settings: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(settings)
    sanitized["api_key"] = ""
    sanitized["provider_api_keys"] = {}
    sanitized["vision_api_key"] = ""
    sanitized.pop("config_path", None)

    raw_headers = sanitized.get("custom_headers")
    if not isinstance(raw_headers, str) or "{api_key}" not in raw_headers:
        sanitized["custom_headers"] = SAFE_CUSTOM_HEADERS

    for field_name in SECRET_FIELD_NAMES:
        if field_name not in sanitized:
            sanitized[field_name] = {} if field_name == "provider_api_keys" else ""
    return sanitized


def _assert_secret_values_absent(payload: bytes, secret_values: set[str]) -> None:
    text = payload.decode("utf-8")
    for value in secret_values:
        if value and value in text:
            raise ValueError("发布设置仍包含源凭据值")


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if temporary_path.read_bytes() != payload:
            raise OSError(f"临时文件回读校验失败：{path.name}")
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成不含 API Key 的完整安装包发布种子")
    parser.add_argument("--source-user-data", type=Path, required=True)
    parser.add_argument("--output-user-data", type=Path, required=True)
    parser.add_argument("--minimum-knowledge-count", type=int, default=400)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    summary = prepare_release_seed(
        args.source_user_data,
        args.output_user_data,
        minimum_knowledge_count=args.minimum_knowledge_count,
    )
    print(json.dumps(summary.as_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
