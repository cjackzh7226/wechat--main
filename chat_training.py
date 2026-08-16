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
from memory import StyleMemory


@dataclass(frozen=True)
class ChatTurn:
    speaker: str
    text: str


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
    """大模型识别导入（正式入口）：把任意格式的知识库文档交给大模型提取问答对。

    - 所有识别出的问答统一归入企业年报总库（source=enterprise_annual_report_kb），
      与已有条目**叠加**（不清空旧库），重复条目由 memory.save 全局去重兜底。
    - 无 API Key 或模型识别失败时直接抛错（不做规则兜底），由 UI 提示用户。
    - ``progress(done, total)``：按"识别分块"上报进度（总块数=所有文档分块之和）。
    """
    if settings is None or not getattr(settings, "api_key", ""):
        raise RuntimeError("未配置文本模型 API Key，无法使用大模型识别知识库")
    poster = post or (requests.post if requests is not None else None)
    if poster is None:
        raise RuntimeError("requests 组件不可用，无法调用文本模型")

    # 第一步：提取所有文件的纯文本并分块（进度不在这里走，块总数用于总进度）
    plan: list[tuple[str, bytes, list[str]]] = []
    files = 0
    for name, raw in _iter_export_files(Path(zip_path)):
        files += 1
        text = _file_to_plain_text(name, raw)
        if not text.strip():
            continue
        chunks = _chunk_text(text)
        if chunks:
            plan.append((name, raw, chunks))

    if not plan:
        if files == 0:
            raise RuntimeError(
                "没有找到支持的文档。支持格式：PDF、Word(docx)、Excel(xlsx/xls)、TXT/MD/CSV/TSV/JSON、HTML"
            )
        raise RuntimeError("未能从文件中提取出文字，请检查文件内容（PDF 若为扫描件需先转文字版）")

    total_chunks = sum(len(chunks) for _name, _raw, chunks in plan)
    done_chunks = 0
    all_examples: list[dict[str, str]] = []
    for name, raw, chunks in plan:
        is_enterprise = bool(
            detect_enterprise_annual_report_workbook(name, raw)
            or _looks_like_enterprise_knowledge(name, _file_to_plain_text(name, raw))
        )
        for chunk in chunks:
            items = _extract_qa_chunk_with_llm(name, chunk, settings, poster, is_enterprise)
            for item in items:
                item["source"] = ENTERPRISE_ANNUAL_REPORT_SOURCE
                item["source_file"] = name
                item["cue"] = f"[对方] {item.get('question', '')}"
                _apply_org_type_tags(item)
            all_examples.extend(items)
            done_chunks += 1
            if progress:
                progress(done_chunks, total_chunks)

    if not all_examples:
        raise RuntimeError("未能从文件中识别出问答对，请检查文件内容是否包含问题与答案")

    selected = _dedupe_examples(all_examples)[:max_examples]
    # 叠加：不清空已有同源条目；重复问答由 memory.save 的全局去重兜底
    memory.load()
    memory.learn_from_training_examples(selected)
    return {
        "files": files,
        "turns": len(selected),
        "examples": len(selected),
    }


def _apply_org_type_tags(item: dict[str, str]) -> None:
    """底层规则：给知识库条目的"匹配上下文/客户说法"（cue）追加机构类型标签，提高命中率。

    - cue（含问题）里提到"民办非企业单位" → 末尾追加 " / 民非"
    - cue 里提到"社会团体" → 末尾追加 " / 社团"
    - 两者都提到 → 依次追加 " / 民非 / 社团"
    - 回答里出现明确条例关键词也判定：社会团体登记管理条例/社会团体年度检查暂行办法 → 社团；
      民办非企业单位登记管理暂行条例/民办非企业单位年度检查办法 → 民非（只认明确条例全名，
      不根据"常务理事会/理事会"之类推断）。
    - 已有相同标签时不重复追加。
    该函数在导入话术库的必经流程中调用（import_chat_zip_to_memory_with_llm）。
    """
    cue = str(item.get("cue", "")).strip()
    text = str(item.get("question", "")) + cue
    reply = str(item.get("reply", ""))
    tags: list[str] = []
    if "民办非企业单位" in text or "民非" in text:
        tags.append("民非")
    if "社会团体" in text or "社团" in text:
        tags.append("社团")
    # 明确条例关键词（独立判定，与 cue 文本互不干扰）：
    if "民办非企业单位登记管理暂行条例" in reply or "民办非企业单位年度检查办法" in reply:
        if "民非" not in tags:
            tags.append("民非")
    if "社会团体登记管理条例" in reply or "社会团体年度检查暂行办法" in reply:
        if "社团" not in tags:
            tags.append("社团")
    for tag in tags:
        if f"/ {tag}" in cue or cue.endswith(tag):
            continue
        cue = f"{cue} / {tag}"
    item["cue"] = cue


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


def _chunk_text(text: str, max_chars: int = 5000) -> list[str]:
    """按段落边界切块，避免单次调用超出模型上下文。

    分块越小，单块问答对越少，模型输出 JSON 越不容易被 max_tokens 截断。
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        if current and len(current) + len(line) + 1 > max_chars:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current.strip():
        chunks.append(current)
    return chunks


def _looks_like_enterprise_knowledge(file_name: str, text: str) -> bool:
    """宽松的企业年报知识库判定：文件名或内容出现年报/年检特征即视为企业知识库。

    大模型导入场景下用户文档格式多样（txt/docx/xls 等），不能只依赖 .xlsx 结构检测。
    """
    name_l = file_name.lower()
    if any(keyword in name_l for keyword in ("年报", "年检", "年审", "annual", "年度报告")):
        return True
    sample = text[:2000]
    return ("机构类型" in sample or "模块" in sample) and "问题" in sample and "答" in sample


def _extract_qa_with_llm(
    file_name: str,
    text: str,
    raw: bytes,
    settings,
    poster,
) -> list[dict[str, str]]:
    is_enterprise = bool(
        detect_enterprise_annual_report_workbook(file_name, raw)
        or _looks_like_enterprise_knowledge(file_name, text)
    )
    items: list[dict[str, str]] = []
    for chunk in _chunk_text(text):
        items.extend(_extract_qa_chunk_with_llm(file_name, chunk, settings, poster, is_enterprise))
    source = "enterprise_annual_report_kb" if is_enterprise else "chat_zip_training"
    for item in items:
        item["source"] = source
        item["cue"] = f"[对方] {item.get('question', '')}"
    return items


def _extract_qa_chunk_with_llm(
    file_name: str,
    chunk: str,
    settings,
    poster,
    is_enterprise: bool,
) -> list[dict[str, str]]:
    extra_rule = (
        "5. 这份文档是企业年报问答表，请额外识别 organization_type（机构类型，如：社会团体/民办非企业单位，"
        "没有则填空字符串）。\n"
        if is_enterprise
        else ""
    )
    messages = [
        {
            "role": "system",
            "content": (
                "你是企业知识库整理助手。只根据用户提供的文档内容提取问答对，不要编造文档中没有的内容。"
                "输出必须是严格 JSON 数组，不要 Markdown。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"请从下面的文档（文件名：{file_name}）中识别出所有“客户问题-标准回答”对，整理成 JSON 数组。\n"
                "每项格式：{\"question\": \"客户会问的问题（保留原文问法）\", "
                "\"reply\": \"标准回答（保留原文完整答案，不要省略、不要改写）\", "
                "\"module\": \"所属分类（如：年检/填报/登录，没有则填空字符串）\"}\n"
                "要求：\n"
                "1. question 用客户角度的自然问法，覆盖文档中的原问题或原说法；\n"
                "2. reply 必须是文档中的完整标准答案，不要缩写；\n"
                "3. 文档里没有明确问答关系的表头、说明文字不要收录；\n"
                "4. 只输出 JSON 数组，不要任何多余文字。\n"
                f"{extra_rule}"
                f"文档内容：\n{chunk}"
            ),
        },
    ]
    request = build_text_generation_request(settings, messages)
    # 输出配额要够大：单块问答对可能十几条，2000 token 会被截断导致 JSON 不完整
    request.payload["max_tokens"] = 4000
    # 超时放宽到 180 秒：长文档 + 长 JSON 输出，智谱 glm 等模型可能超过 60 秒
    try:
        response = poster(request.url, headers=request.headers, json=request.payload, timeout=180)
    except Exception as exc:
        raise RuntimeError(f"大模型识别失败（调用异常）：{exc}") from exc
    if int(getattr(response, "status_code", 200) or 200) >= 400:
        raise RuntimeError(f"大模型识别失败：HTTP {getattr(response, 'status_code', '')}")
    try:
        response.raise_for_status()
        content = extract_text_from_response(response.json(), settings)
    except Exception as exc:
        raise RuntimeError(f"大模型识别失败（响应解析异常）：{exc}") from exc
    parsed = _parse_qa_json(content)
    return parsed


def _parse_qa_json(content: str) -> list[dict[str, str]]:
    text = (content or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"大模型输出不是有效 JSON：{str(exc)[:120]}") from exc
    if not isinstance(data, list):
        raise RuntimeError("大模型输出不是 JSON 数组")
    items: list[dict[str, str]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        question = str(row.get("question", "")).strip()
        reply = str(row.get("reply", "")).strip()
        if not question or not reply:
            continue
        item: dict[str, str] = {"question": question, "reply": reply}
        module = str(row.get("module", "")).strip()
        if module:
            item["module"] = module
        org_type = str(row.get("organization_type", "")).strip()
        if org_type:
            item["organization_type"] = org_type
        items.append(item)
    if not items:
        raise RuntimeError("大模型识别结果为空（未找到问答对）")
    return items


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
