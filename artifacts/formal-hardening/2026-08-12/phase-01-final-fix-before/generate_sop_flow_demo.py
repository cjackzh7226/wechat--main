from __future__ import annotations

import json
from pathlib import Path

from memory import StyleMemory
from sop_analyzer import (
    FLOW_LIBRARY_VERSION,
    build_sop_library_from_memory,
    ensure_sop_library_flow,
    format_sop_library_html,
    save_sop_library,
)


def _load_library() -> dict:
    memory = StyleMemory()
    path = memory.sop_library_path
    if not path.exists():
        return {"stats": {}, "scenes": [], "steps": []}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not data.get("flow_nodes") or int(data.get("flow_version", 0) or 0) < FLOW_LIBRARY_VERSION:
        memory.load()
        if memory.examples:
            data = build_sop_library_from_memory(memory)
        else:
            data = ensure_sop_library_flow(data)
        save_sop_library(data, path)
    return data


def _build_html(data: dict) -> str:
    return format_sop_library_html(data)


def main() -> None:
    output = Path(__file__).with_name("sop_flow_report_demo.html")
    output.write_text(_build_html(_load_library()), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
