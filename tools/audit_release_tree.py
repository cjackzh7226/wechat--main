from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .prepare_release_seed import _collect_secret_values
except ImportError:  # 直接以脚本方式运行时，tools 目录本身在 sys.path 中。
    from prepare_release_seed import _collect_secret_values


FORBIDDEN_FILE_NAMES = {"credentials.dat"}


def audit_release_tree(source_settings: Path, release_tree: Path) -> dict[str, int]:
    source_settings = source_settings.resolve(strict=True)
    release_tree = release_tree.resolve(strict=True)
    settings = json.loads(source_settings.read_text(encoding="utf-8"))
    if not isinstance(settings, dict):
        raise ValueError("源 settings.json 的顶层结构不是对象")

    secret_values = _collect_secret_values(settings)
    patterns = _recognizable_patterns(secret_values)
    files_scanned = 0
    forbidden_file_count = 0
    secret_match_count = 0

    for path in release_tree.rglob("*"):
        if not path.is_file():
            continue
        files_scanned += 1
        if path.name.casefold() in FORBIDDEN_FILE_NAMES:
            forbidden_file_count += 1
        payload = path.read_bytes()
        if any(pattern in payload for pattern in patterns):
            secret_match_count += 1

    if forbidden_file_count or secret_match_count:
        raise ValueError(
            "发布目录凭据审计失败："
            f"forbidden_file_count={forbidden_file_count}, "
            f"secret_match_count={secret_match_count}"
        )
    return {
        "files_scanned": files_scanned,
        "forbidden_file_count": 0,
        "secret_match_count": 0,
    }


def _recognizable_patterns(secret_values: set[str]) -> set[bytes]:
    patterns: set[bytes] = set()
    for value in secret_values:
        candidates = {value}
        if len(value) >= 24:
            candidates.update({value[:12], value[-12:]})
        for candidate in candidates:
            if not candidate:
                continue
            patterns.add(candidate.encode("utf-8"))
            patterns.add(candidate.encode("utf-16-le"))
            patterns.add(candidate.encode("utf-16-be"))
    return patterns


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="扫描发布目录是否含源 API Key 或凭据文件")
    parser.add_argument("--source-settings", type=Path, required=True)
    parser.add_argument("--release-tree", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    result = audit_release_tree(args.source_settings, args.release_tree)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
