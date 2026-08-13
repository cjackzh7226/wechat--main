from __future__ import annotations

import csv
import html
import importlib
import json
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    import requests
except Exception:  # pragma: no cover - optional dependency
    requests = None

from enterprise_kb import (
    ENTERPRISE_ANNUAL_REPORT_SOURCE,
    detect_enterprise_annual_report_workbook,
    parse_enterprise_annual_report_workbook,
)
from llm_clients import build_text_generation_request, extract_text_from_response
from knowledge_base import KnowledgeAudit
from knowledge_import import (
    ImportModelError,
    ImportReport,
    activate_import,
    stage_import,
)
from memory import StyleMemory


@dataclass(frozen=True)
class ChatTurn:
    speaker: str
    text: str


class KnowledgeImportModelClient:
    """Extract source-linked rows without splitting or accepting generated answers."""

    def __init__(self, settings, post=None) -> None:
        self.settings = settings
        self.poster = post or (requests.post if requests is not None else None)

    def extract(self, text: str, *, source_file: str) -> list[dict[str, object]]:
        if not getattr(self.settings, "api_key", ""):
            raise ImportModelError("未配置文本模型 API Key，无法提取非结构化知识文档")
        if self.poster is None:
            raise ImportModelError("requests 组件不可用，无法调用文本模型")
        messages = [
            {
                "role": "system",
                "content": (
                    "你只做来源定位，不生成、不改写答案。输出必须是严格 JSON 数组，"
                    "不得使用 Markdown 或解释文字。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"从文件 {source_file} 的完整原文中识别问答。每项只允许字段："
                    "question、reply、source_excerpt、keywords。"
                    "reply 必须逐字来自 source_excerpt；source_excerpt 必须是下方原文中连续存在的原文；"
                    "机构类型由程序根据来源原文判定，模型不要填写。无法逐字定位的内容不要补写。\n"
                    f"完整原文：\n{text}"
                ),
            },
        ]
        request = build_text_generation_request(self.settings, messages)
        request.payload["temperature"] = 0
        request.payload["max_tokens"] = 4000
        try:
            response = self.poster(
                request.url,
                headers=request.headers,
                json=request.payload,
                timeout=180,
            )
            if int(getattr(response, "status_code", 200) or 200) >= 400:
                raise RuntimeError(f"HTTP {getattr(response, 'status_code', '')}")
            response.raise_for_status()
            content = extract_text_from_response(response.json(), self.settings)
            rows = json.loads((content or "").strip())
        except Exception as error:
            raise ImportModelError(
                f"非结构化知识提取失败（{type(error).__name__}）"
            ) from error
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ImportModelError("非结构化知识提取结果必须是 JSON 对象数组")
        return rows


class _MemoryKnowledgeImportRepository:
    def __init__(self, memory: StyleMemory) -> None:
        self.memory = memory

    def current_records(self):
        return self.memory.knowledge_records()

    def replace_records_atomic(self, audit: KnowledgeAudit) -> None:
        self.memory.replace_knowledge_audit_atomic(audit)


def stage_training_import(
    path: Path | str,
    settings,
    *,
    post=None,
    progress=None,
) -> ImportReport:
    """Stage a whole-document import; structured files never call the model."""
    report = stage_import(Path(path), KnowledgeImportModelClient(settings, post=post))
    if progress:
        progress(len(report.completed_files), len(report.completed_files))
    return report


def activate_training_import(
    report: ImportReport, memory: StyleMemory
) -> KnowledgeAudit:
    return activate_import(report, _MemoryKnowledgeImportRepository(memory))


def parse_ab_chat_text(text: str) -> list[ChatTurn]:
    turns: list[ChatTurn] = []
    current_speaker = ""
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_speaker, current_lines
        body = "\n".join(line.strip() for line in current_lines if line.strip()).strip()
        if current_speaker and body:
            turns.append(ChatTurn(current_speaker, body))
        current_speaker = ""
        current_lines = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^([AB])\s*[:：]\s*(.*)$", line, re.I)
        if match:
            flush()
            current_speaker = match.group(1).upper()
            current_lines = [match.group(2).strip()]
            continue
        if current_speaker:
            current_lines.append(line)
    flush()
    return turns


def parse_qa_chat_text(text: str) -> list[ChatTurn]:
    turns: list[ChatTurn] = []
    question = ""
    answer = ""

    def flush() -> None:
        nonlocal question, answer
        if question.strip() and answer.strip():
            turns.extend([ChatTurn("A", question.strip()), ChatTurn("B", answer.strip())])
        question = ""
        answer = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        question_match = re.match(r"^(问题|客户问题|用户问题|问|Q)\s*[:：]\s*(.+)$", line, re.I)
        if question_match:
            flush()
            question = question_match.group(2).strip()
            continue
        answer_match = re.match(r"^(回复|回答|答案|话术|标准回复|标准话术|A)\s*[:：]\s*(.+)$", line, re.I)
        if answer_match:
            answer = answer_match.group(2).strip()
            continue
        if answer:
            answer = f"{answer}\n{line}".strip()
        elif question:
            question = f"{question}\n{line}".strip()
    flush()
    return turns


def parse_two_person_chat_text(text: str, own_names: Iterable[str] = ("我",)) -> list[ChatTurn]:
    raw_turns: list[tuple[str, str]] = []
    current_name = ""
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_name, current_lines
        body = "\n".join(line.strip() for line in current_lines if line.strip()).strip()
        if current_name and body:
            raw_turns.append((current_name, body))
        current_name = ""
        current_lines = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parsed = _parse_named_message_line(line)
        if parsed:
            flush()
            current_name, body = parsed
            current_lines = [body]
            continue
        if current_name:
            current_lines.append(line)
    flush()

    speaker_names = []
    for name, _text in raw_turns:
        if name not in speaker_names:
            speaker_names.append(name)
    if len(speaker_names) != 2:
        return []

    own = {name.strip() for name in own_names if name and name.strip()}
    assistant_name = _choose_assistant_name(speaker_names, raw_turns, own)
    return [ChatTurn("B" if name == assistant_name else "A", text) for name, text in raw_turns]


def parse_wechatbak_txt(text: str, own_names: Iterable[str] = ("我",)) -> list[ChatTurn]:
    own = {name.strip() for name in own_names if name and name.strip()}
    turns: list[ChatTurn] = []
    current_speaker = ""
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_speaker, current_lines
        body = "\n".join(line.strip() for line in current_lines if line.strip()).strip()
        if current_speaker and body:
            turns.append(ChatTurn(current_speaker, body))
        current_speaker = ""
        current_lines = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("WechatBakTool") or line.startswith("===") or line.startswith("导出时间"):
            continue
        match = re.match(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s*\|\s*([^:：]{1,80})[:：]\s*(.*)$", line)
        if match:
            flush()
            sender = match.group(1).strip()
            current_speaker = "B" if sender in own else "A"
            current_lines = [match.group(2).strip()]
            continue
        if current_speaker:
            current_lines.append(line)
    flush()
    return turns


def parse_message_rows(rows: Iterable[dict[str, object]], own_names: Iterable[str] = ("我",)) -> list[ChatTurn]:
    own = {name.strip() for name in own_names if name and name.strip()}
    turns: list[ChatTurn] = []
    for row in rows:
        lowered = {str(key).strip().lower(): value for key, value in row.items()}
        sender = _first_value(lowered, ["sender", "发送人", "昵称", "姓名", "用户", "客服", "nickname", "name", "from", "talker"])
        content = _first_value(lowered, ["content", "消息", "内容", "文字消息", "文本", "text", "message", "strcontent"])
        if not content:
            continue
        speaker = "B" if str(sender).strip() in own else "A"
        turns.append(ChatTurn(speaker, str(content).strip()))
    return turns


def extract_reply_examples(turns: Iterable[ChatTurn], assistant_speaker: str = "B") -> list[dict[str, str]]:
    items = list(turns)
    examples: list[dict[str, str]] = []
    for index, turn in enumerate(items):
        if turn.speaker != assistant_speaker:
            continue
        reply = _clean_training_text(turn.text)
        if not _is_useful_reply(reply):
            continue
        cue = _build_cue(items[max(0, index - 8) : index])
        if not cue:
            continue
        examples.append(
            {
                "partner": "聊天记录训练",
                "cue": cue,
                "reply": reply,
                "source": "chat_zip_training",
            }
        )
    return examples


def extract_phrasebook_examples(rows: Iterable[dict[str, object]], source: str = "chat_zip_training") -> list[dict[str, str]]:
    examples: list[dict[str, str]] = []
    for row in rows:
        normalized = {_normalize_column_name(str(key)): value for key, value in row.items()}
        reply = _first_normalized_value(
            normalized,
            [
                "话术内容必填",
                "话术内容",
                "回复内容",
                "回复",
                "回答",
                "答案",
                "标准回复",
                "标准话术",
                "内容",
                "message",
                "reply",
                "answer",
            ],
        )
        reply = _clean_training_text(reply)
        if not _is_useful_reply(reply):
            continue

        cue_parts = [
            _first_normalized_value(normalized, ["一级分类必填", "一级分类", "分类", "category"]),
            _first_normalized_value(normalized, ["二级分类选填", "二级分类", "子分类", "subcategory"]),
            _first_normalized_value(
                normalized,
                [
                    "话术标题选填",
                    "话术标题",
                    "标题",
                    "场景",
                    "问题",
                    "客户问题",
                    "用户问题",
                    "关键词",
                    "cue",
                    "prompt",
                    "question",
                    "title",
                ],
            ),
        ]
        cue = " / ".join(part for part in (_clean_training_text(item) for item in cue_parts) if part)
        if not cue:
            cue = reply[:40]
        examples.append(
            {
                "partner": "话术库训练",
                "cue": cue,
                "reply": reply,
                "source": source,
            }
        )
    return examples


def import_chat_zip_to_memory(zip_path: Path | str, memory: StyleMemory, max_examples: int = 3000) -> dict[str, int]:
    return _import_training_files(Path(zip_path), memory, max_examples, source="chat_zip_training", own_names=("我",))


def import_wechat_exports_to_memory(
    source_path: Path | str,
    memory: StyleMemory,
    max_examples: int = 3000,
    own_names: Iterable[str] = ("我",),
) -> dict[str, int]:
    return _import_training_files(Path(source_path), memory, max_examples, source="wechat_export_training", own_names=own_names)


def _import_training_files(
    path: Path,
    memory: StyleMemory,
    max_examples: int,
    source: str,
    own_names: Iterable[str],
) -> dict[str, int]:
    all_examples: list[dict[str, str]] = []
    files = 0
    turns_count = 0

    for name, raw in _iter_export_files(path):
        files += 1
        enterprise_examples = parse_enterprise_annual_report_workbook(name, raw)
        if enterprise_examples:
            turns_count += len(enterprise_examples)
            all_examples.extend(enterprise_examples)
            continue
        turns = _parse_export_file(name, raw, own_names)
        if turns:
            turns_count += len(turns)
            for item in extract_reply_examples(turns):
                item["source"] = source
                all_examples.append(item)
            continue
        phrasebook_examples = _parse_phrasebook_file(name, raw, source)
        if phrasebook_examples:
            turns_count += len(phrasebook_examples)
            all_examples.extend(phrasebook_examples)

    selected_by_source: dict[str, list[dict[str, str]]] = {}
    for item in all_examples:
        item_source = str(item.get("source") or source)
        selected_by_source.setdefault(item_source, []).append(item)

    selected_by_source = {
        item_source: _dedupe_examples(items)[:max_examples]
        for item_source, items in selected_by_source.items()
    }
    memory.load()
    for item_source, selected in selected_by_source.items():
        if not selected:
            continue
        memory.examples = [item for item in memory.examples if item.get("source") != item_source]
        memory.learn_from_training_examples(selected)
    return {
        "files": files,
        "turns": turns_count,
        "examples": sum(len(items) for items in selected_by_source.values()),
    }


def import_chat_zip_to_memory_with_llm(
    zip_path: Path | str,
    memory: StyleMemory,
    settings,
    max_examples: int = 3000,
    post=None,
    progress=None,
) -> dict[str, int]:
    """Compatibility wrapper around the whole-document transactional importer.

    ``max_examples`` is intentionally ignored: formal imports have no 3,000-record
    truncation boundary.
    """
    del max_examples
    report = stage_training_import(
        Path(zip_path), settings, post=post, progress=progress
    )
    activate_training_import(report, memory)
    return {
        "files": len(report.completed_files),
        "turns": len(report.records),
        "examples": len(report.records),
    }


def _file_to_plain_text(name: str, raw: bytes) -> str:
    """只做文件→纯文本提取（不识别格式），识别交给大模型。"""
    suffix = Path(name).suffix.lower()
    if suffix in {".txt", ".md", ".csv", ".tsv", ".json"}:
        return _decode_chat_bytes(raw)
    if suffix in {".html", ".htm"}:
        return _html_text(_decode_chat_bytes(raw))
    if suffix == ".docx":
        return _docx_text(raw)
    if suffix == ".xlsx":
        return _xlsx_text(raw)
    if suffix == ".xls":
        return _xls_text(raw)
    if suffix == ".pdf":
        return _pdf_text(raw)
    return ""


def _pdf_text(raw: bytes) -> str:
    """PDF → 纯文本（逐页提取，保留阅读顺序；表格文字也会被提取出来）。"""
    try:
        from pypdf import PdfReader
        from io import BytesIO
        reader = PdfReader(BytesIO(raw))
        pages = []
        for page in reader.pages:
            try:
                pages.append(str(page.extract_text() or ""))
            except Exception:
                continue
        return "\n".join(pages).strip()
    except Exception as exc:
        raise RuntimeError(f"PDF 文本提取失败：{exc}") from exc


def _decode_chat_bytes(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _iter_export_files(path: Path) -> Iterable[tuple[str, bytes]]:
    supported = {".txt", ".md", ".csv", ".tsv", ".json", ".docx", ".xlsx", ".xls", ".html", ".htm", ".pdf"}
    if path.is_dir():
        for item in path.rglob("*"):
            if item.is_file() and item.suffix.lower() in supported:
                yield item.name, item.read_bytes()
        return
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if Path(name).suffix.lower() in supported:
                    yield name, archive.read(name)
        return
    if path.is_file() and path.suffix.lower() in supported:
        yield path.name, path.read_bytes()


def _parse_export_file(name: str, raw: bytes, own_names: Iterable[str]) -> list[ChatTurn]:
    suffix = Path(name).suffix.lower()
    text = _decode_chat_bytes(raw)
    if suffix in {".txt", ".md"}:
        return (
            parse_wechatbak_txt(text, own_names)
            or parse_ab_chat_text(text)
            or parse_qa_chat_text(text)
            or parse_two_person_chat_text(text, own_names)
        )
    if suffix in {".html", ".htm"}:
        html_text = _html_text(text)
        return (
            parse_wechatbak_txt(html_text, own_names)
            or parse_ab_chat_text(html_text)
            or parse_qa_chat_text(html_text)
            or parse_two_person_chat_text(html_text, own_names)
        )
    if suffix == ".csv":
        return parse_message_rows(list(csv.DictReader(text.splitlines())), own_names)
    if suffix == ".tsv":
        return parse_message_rows(list(csv.DictReader(text.splitlines(), delimiter="\t")), own_names)
    if suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        if isinstance(data, dict):
            data = data.get("messages") or data.get("data") or []
        if isinstance(data, list):
            return parse_message_rows([item for item in data if isinstance(item, dict)], own_names)
    if suffix == ".docx":
        docx_text = _docx_text(raw)
        return parse_ab_chat_text(docx_text) or parse_qa_chat_text(docx_text) or parse_two_person_chat_text(docx_text, own_names)
    if suffix == ".xlsx":
        xlsx_text = _xlsx_text(raw)
        return (
            parse_ab_chat_text(xlsx_text)
            or parse_qa_chat_text(xlsx_text)
            or parse_two_person_chat_text(xlsx_text, own_names)
            or parse_message_rows(list(csv.DictReader(xlsx_text.splitlines())), own_names)
        )
    if suffix == ".xls":
        xls_text = _xls_text(raw)
        return (
            parse_ab_chat_text(xls_text)
            or parse_qa_chat_text(xls_text)
            or parse_two_person_chat_text(xls_text, own_names)
            or parse_message_rows(list(csv.DictReader(xls_text.splitlines())), own_names)
        )
    return []


def _parse_phrasebook_file(name: str, raw: bytes, source: str) -> list[dict[str, str]]:
    enterprise_examples = parse_enterprise_annual_report_workbook(name, raw)
    if enterprise_examples:
        return enterprise_examples
    examples = extract_phrasebook_examples(_table_rows_from_export(name, raw), source=source)
    if examples:
        return examples
    if Path(name).suffix.lower() == ".docx":
        return _qa_turns_to_examples(parse_qa_chat_text(_docx_text(raw)), source)
    return []


def _parse_named_message_line(line: str) -> tuple[str, str] | None:
    cleaned = re.sub(r"^\[?\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?\]?\s*", "", line)
    cleaned = re.sub(r"^\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s*", "", cleaned)
    match = re.match(r"^([^:：]{1,30})[:：]\s*(.+)$", cleaned)
    if match:
        name = match.group(1).strip()
        body = match.group(2).strip()
    else:
        spaced = re.match(r"^([\u4e00-\u9fffA-Za-z0-9_昵称客服老师助理小智顾问运营销售店长\-]{1,20})\s+(.{2,})$", cleaned)
        if not spaced:
            return None
        name = spaced.group(1).strip()
        body = spaced.group(2).strip()
    if not name or not body:
        return None
    if name in {"问题", "回复", "回答", "答案", "A", "B", "Q"}:
        return None
    if re.search(r"(http|www\.|\.com|\.cn)", name, re.I):
        return None
    return name, body


def _choose_assistant_name(speaker_names: list[str], turns: list[tuple[str, str]], own_names: set[str]) -> str:
    for name in speaker_names:
        if name in own_names:
            return name
    assistant_markers = ["我", "客服", "老师", "助理", "小智", "顾问", "运营", "销售", "店长"]
    for name in speaker_names:
        if any(marker in name for marker in assistant_markers):
            return name
    if len(turns) >= 2:
        return turns[1][0]
    return speaker_names[-1]


def _qa_turns_to_examples(turns: list[ChatTurn], source: str) -> list[dict[str, str]]:
    examples = extract_reply_examples(turns)
    for item in examples:
        item["source"] = source
    return examples


def _table_rows_from_export(name: str, raw: bytes) -> list[dict[str, object]]:
    suffix = Path(name).suffix.lower()
    if suffix == ".csv":
        return list(csv.DictReader(_decode_chat_bytes(raw).splitlines()))
    if suffix == ".tsv":
        return list(csv.DictReader(_decode_chat_bytes(raw).splitlines(), delimiter="\t"))
    if suffix == ".json":
        try:
            data = json.loads(_decode_chat_bytes(raw))
        except json.JSONDecodeError:
            return []
        if isinstance(data, dict):
            data = data.get("messages") or data.get("data") or data.get("rows") or []
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    if suffix == ".xlsx":
        return list(csv.DictReader(_xlsx_text(raw).splitlines()))
    if suffix == ".xls":
        return list(csv.DictReader(_xls_text(raw).splitlines()))
    return []


def _html_text(text: str) -> str:
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6])\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text)


def _docx_text(raw: bytes) -> str:
    lines: list[str] = []
    with zipfile.ZipFile(_bytes_path(raw)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    for paragraph in re.findall(r"<w:p\b.*?</w:p>", xml, re.S):
        parts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", paragraph, re.S)
        text = html.unescape("".join(parts)).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def _xlsx_text(raw: bytes) -> str:
    rows_out: list[str] = []
    with zipfile.ZipFile(_bytes_path(raw)) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_xml = archive.read("xl/sharedStrings.xml").decode("utf-8", errors="ignore")
            for item in re.findall(r"<si\b.*?</si>", shared_xml, re.S):
                parts = re.findall(r"<t[^>]*>(.*?)</t>", item, re.S)
                shared.append(html.unescape("".join(parts)).strip())
        sheet_names = sorted(name for name in archive.namelist() if name.startswith("xl/worksheets/") and name.endswith(".xml"))
        for sheet_name in sheet_names:
            sheet_xml = archive.read(sheet_name).decode("utf-8", errors="ignore")
            for row in re.findall(r"<row\b.*?</row>", sheet_xml, re.S):
                values: list[str] = []
                for cell in re.findall(r"<c\b(.*?)</c>", row, re.S):
                    attr, body = cell.split(">", 1) if ">" in cell else ("", cell)
                    value_match = re.search(r"<v>(.*?)</v>", body, re.S)
                    inline_match = re.search(r"<t[^>]*>(.*?)</t>", body, re.S)
                    value = ""
                    if value_match:
                        raw_value = html.unescape(value_match.group(1).strip())
                        if 't="s"' in attr:
                            try:
                                value = shared[int(raw_value)]
                            except Exception:
                                value = raw_value
                        else:
                            value = raw_value
                    elif inline_match:
                        value = html.unescape(inline_match.group(1).strip())
                    values.append(value)
                if any(values):
                    rows_out.append(",".join(values))
    return "\n".join(rows_out)


def _xls_text(raw: bytes) -> str:
    try:
        xlrd = _ensure_package_import("xlrd", "xlrd>=2.0.1")
    except Exception as exc:
        raise RuntimeError(
            "当前环境缺少 xlrd，程序已尝试自动安装但失败，无法读取 .xls 文件。"
            "请确认网络可用，或重新打开桌面启动程序后再导入。"
        ) from exc
    workbook = xlrd.open_workbook(file_contents=raw)
    lines: list[str] = []
    for sheet in workbook.sheets():
        for row_index in range(sheet.nrows):
            values = []
            for col_index in range(sheet.ncols):
                value = sheet.cell_value(row_index, col_index)
                if value is None:
                    continue
                text = str(value).strip()
                if text.endswith(".0"):
                    text = text[:-2]
                values.append(text)
            if any(values):
                lines.append(",".join(values))
    return "\n".join(lines)


def _ensure_package_import(module_name: str, package_spec: str):
    try:
        return importlib.import_module(module_name)
    except ImportError:
        if getattr(sys, "frozen", False):
            raise
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", package_spec],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        importlib.invalidate_caches()
        return importlib.import_module(module_name)


def _bytes_path(raw: bytes):
    import io

    return io.BytesIO(raw)


def _first_value(row: dict[str, object], keys: list[str]) -> str:
    for key in keys:
        value = row.get(key.lower())
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _first_normalized_value(row: dict[str, object], keys: list[str]) -> str:
    for key in keys:
        value = row.get(_normalize_column_name(key))
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _normalize_column_name(name: str) -> str:
    return re.sub(r"[\s:：()（）【】\[\]、，,。.!！?？]+", "", name).lower()


def _build_cue(previous_turns: list[ChatTurn]) -> str:
    useful = []
    for turn in previous_turns[-8:]:
        text = _clean_training_text(turn.text)
        if not text or _is_media_only(text):
            continue
        role = "对方" if turn.speaker == "A" else "我"
        useful.append(f"[{role}] {text}")
    return "\n".join(useful)


def _clean_training_text(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def _is_useful_reply(text: str) -> bool:
    if not text or _is_media_only(text):
        return False
    if len(text) < 2 or len(text) > 1000:
        return False
    noisy_markers = ["我已经添加了你", "朋友圈", "系统消息", "有看到消息吗", "看到消息了吗"]
    return not any(marker in text for marker in noisy_markers)


def _is_media_only(text: str) -> bool:
    return bool(re.fullmatch(r"[【\[]?(产品图片|图片|视频|语音|表情|链接)[】\]]?", text.strip()))


def _dedupe_examples(examples: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for item in examples:
        key = re.sub(r"\W+", "", f"{item.get('cue', '')}{item.get('reply', '')}")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def parse_message_rows(rows: Iterable[dict[str, object]], own_names: Iterable[str] = ("我",)) -> list[ChatTurn]:
    materialized = [row for row in rows if isinstance(row, dict)]
    if not materialized:
        return []

    sender_key, content_key = _infer_sender_and_content_columns(materialized)
    if not content_key:
        return []

    named_turns: list[tuple[str, str]] = []
    for row in materialized:
        content = str(row.get(content_key, "") or "").strip()
        sender = str(row.get(sender_key, "") or "").strip() if sender_key else ""
        if sender and content:
            named_turns.append((sender, content))

    speaker_names: list[str] = []
    for name, _text in named_turns:
        if name not in speaker_names:
            speaker_names.append(name)
    if len(speaker_names) != 2:
        return []

    assistant_name = _choose_assistant_name(
        speaker_names,
        named_turns,
        {name.strip() for name in own_names if name and name.strip()},
    )
    return [ChatTurn("B" if name == assistant_name else "A", text) for name, text in named_turns]


def _infer_sender_and_content_columns(rows: list[dict[str, object]]) -> tuple[str, str]:
    keys: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in keys:
                keys.append(str(key))
    if not keys:
        return "", ""

    normalized = {_normalize_column_name(key): key for key in keys}
    sender_names = [
        "sender",
        "发送人",
        "发送方",
        "说话人",
        "发言人",
        "昵称",
        "姓名",
        "用户",
        "客户",
        "客服",
        "角色",
        "name",
        "from",
        "talker",
    ]
    content_names = [
        "content",
        "消息",
        "消息内容",
        "聊天内容",
        "内容",
        "文字消息",
        "文本",
        "话",
        "发言",
        "message",
        "text",
        "strcontent",
    ]
    sender_key = _first_matching_column(normalized, sender_names)
    content_key = _first_matching_column(normalized, content_names)
    if sender_key and content_key:
        return sender_key, content_key

    stats = []
    for key in keys:
        values = [str(row.get(key, "") or "").strip() for row in rows]
        non_empty = [value for value in values if value]
        if not non_empty:
            continue
        unique_count = len(set(non_empty))
        avg_len = sum(len(value) for value in non_empty) / len(non_empty)
        stats.append((key, unique_count, avg_len, len(non_empty)))

    if not sender_key:
        sender_candidates = [
            item
            for item in stats
            if 1 < item[1] <= 6 and item[2] <= 18 and item[3] >= max(2, len(rows) // 2)
        ]
        if sender_candidates:
            sender_key = sorted(sender_candidates, key=lambda item: (item[1], item[2]))[0][0]

    if not content_key:
        content_candidates = [item for item in stats if item[0] != sender_key and item[2] >= 2]
        if content_candidates:
            content_key = sorted(content_candidates, key=lambda item: item[2], reverse=True)[0][0]

    return sender_key or "", content_key or ""


def _first_matching_column(normalized_columns: dict[str, str], names: list[str]) -> str:
    for name in names:
        key = _normalize_column_name(name)
        if key in normalized_columns:
            return normalized_columns[key]
    for key, original in normalized_columns.items():
        if any(_normalize_column_name(name) in key for name in names):
            return original
    return ""


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Import chat records into local style memory.")
    parser.add_argument("path", help="Zip/txt/csv/json/docx/xlsx/xls file or folder containing chat records.")
    parser.add_argument("--max-examples", type=int, default=3000)
    parser.add_argument("--format", choices=["ab_zip", "wechat_export"], default="wechat_export")
    parser.add_argument("--own-name", action="append", default=["我"], help="Sender name treated as yourself.")
    args = parser.parse_args()

    if args.format == "ab_zip":
        stats = import_chat_zip_to_memory(Path(args.path), StyleMemory(), args.max_examples)
    else:
        stats = import_wechat_exports_to_memory(Path(args.path), StyleMemory(), args.max_examples, args.own_name)
    print(f"files={stats['files']} turns={stats['turns']} examples={stats['examples']}")
