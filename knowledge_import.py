from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Protocol

from app_paths import data_dir
from atomic_json import atomic_write_json
from knowledge_base import (
    KnowledgeAudit,
    KnowledgeRecord,
    OrgScope,
    RecordStatus,
    audit_records,
    record_from_example,
)


MAX_MODEL_INPUT_CHARS = 12_000
OVER_LIMIT_ERROR = "输入超过模型上限，未拆分、未截断、未导入"
IMPORT_STAGE_ENV = "WECHAT_ASSISTANT_IMPORT_STAGE"
IMPORT_STAGE_SCHEMA_VERSION = 1
_STRUCTURED_SUFFIXES = frozenset({".xlsx", ".xls", ".csv", ".tsv"})
_SUPPORTED_SUFFIXES = _STRUCTURED_SUFFIXES | frozenset(
    {".txt", ".md", ".json", ".html", ".htm", ".docx", ".pdf"}
)
_SCOPES = frozenset({"social", "mfei", "both", "unknown"})
_STATUSES = frozenset({"active", "conflict", "pending", "inactive"})
_QUESTION_HEADERS = frozenset({"问题", "客户问题", "问法", "question", "cue"})
_ANSWER_HEADERS = frozenset({"答案", "标准答案", "回复", "reply", "answer"})
_ORG_HEADERS = frozenset({"机构类型", "组织类型", "organization_type"})
_SCOPE_HEADERS = frozenset({"机构范围", "org_scope", "scope"})
_STATUS_HEADERS = frozenset({"状态", "status"})
_KEYWORD_HEADERS = frozenset({"关键词", "keywords", "分类", "module"})
_MODEL_FIELDS = frozenset(
    {
        "question",
        "reply",
        "source_excerpt",
        "org_scope",
        "organization_type",
        "keywords",
    }
)


class ImportActivationError(RuntimeError):
    """Staged knowledge cannot be committed without risking a partial import."""


class ImportCheckpointError(RuntimeError):
    """The staging checkpoint target could overwrite trusted or source data."""


class ImportModelError(RuntimeError):
    """A user-safe model configuration or transport failure."""


class ImportModel(Protocol):
    def extract(
        self, text: str, *, source_file: str
    ) -> list[dict[str, object]]: ...


@dataclass(frozen=True)
class ImportRecord:
    question: str
    reply: str
    source_file: str
    source_locator: str
    source_excerpt: str
    source_hash: str
    org_scope: OrgScope
    status: RecordStatus
    organization_type: str = ""
    keywords: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.question, str) or not self.question.strip():
            raise ValueError("question must be a non-empty string")
        if not isinstance(self.reply, str) or not self.reply.strip():
            raise ValueError("reply must be a non-empty string")
        for name in (
            "source_file",
            "source_locator",
            "source_excerpt",
            "source_hash",
            "organization_type",
        ):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} must be a string")
        if self.org_scope not in _SCOPES:
            raise ValueError("invalid org_scope")
        if self.status not in _STATUSES:
            raise ValueError("invalid status")
        if self.org_scope == "unknown" and self.status == "active":
            raise ValueError("unknown org_scope cannot be active")
        if not isinstance(self.keywords, tuple) or any(
            not isinstance(keyword, str) or not keyword.strip()
            for keyword in self.keywords
        ):
            raise ValueError("keywords must be a tuple of non-empty strings")
        expected_hash = hashlib.sha256(self.source_excerpt.encode("utf-8")).hexdigest()
        if self.source_hash != expected_hash:
            raise ValueError("source_hash does not match source_excerpt")
        if self.status == "active" and not all(
            value.strip()
            for value in (self.source_file, self.source_locator, self.source_excerpt)
        ):
            raise ValueError("active import record requires verified provenance")

    def to_knowledge_record(self) -> KnowledgeRecord:
        return record_from_example(
            {
                "question": self.question,
                "reply": self.reply,
                "source_file": self.source_file,
                "source_locator": self.source_locator,
                "source_excerpt": self.source_excerpt,
                "org_scope": self.org_scope,
                "status": self.status,
                "organization_type": self.organization_type,
                "keywords": self.keywords,
            }
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "question": self.question,
            "reply": self.reply,
            "source_file": self.source_file,
            "source_locator": self.source_locator,
            "source_excerpt": self.source_excerpt,
            "source_hash": self.source_hash,
            "org_scope": self.org_scope,
            "status": self.status,
            "organization_type": self.organization_type,
            "keywords": list(self.keywords),
        }


@dataclass
class ImportReport:
    records: list[ImportRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    model_calls: int = 0
    completed_files: list[str] = field(default_factory=list)
    checkpoint_path: Path | None = None

    @property
    def active_count(self) -> int:
        return sum(record.status == "active" for record in self.records)

    @property
    def pending_count(self) -> int:
        return sum(record.status == "pending" for record in self.records)

    def to_checkpoint(self) -> dict[str, object]:
        return {
            "schema_version": IMPORT_STAGE_SCHEMA_VERSION,
            "records": [record.to_dict() for record in self.records],
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "model_calls": self.model_calls,
            "completed_files": list(self.completed_files),
        }


@dataclass(frozen=True)
class _SourceFile:
    name: str
    raw: bytes

    @property
    def suffix(self) -> str:
        return Path(self.name).suffix.lower()


@dataclass(frozen=True)
class _DocumentText:
    text: str
    segments: tuple[tuple[str, str], ...]
    errors: tuple[str, ...] = ()


def _normalize_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _source_hash(excerpt: str) -> str:
    return hashlib.sha256(excerpt.encode("utf-8")).hexdigest()


def _scope_from_verified_excerpt(excerpt: str) -> OrgScope:
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", excerpt))
    if re.search(r"(?:不是|并非|不属于|不适用于|是否|还是|或者|转为|变更为)", compact):
        return "unknown"
    has_social = any(marker in compact for marker in ("社会团体", "社团", "协会", "商会"))
    has_mfei = any(marker in compact for marker in ("民办非企业", "民非", "社会服务机构"))
    if has_social and has_mfei:
        if re.search(
            r"(?:两类(?:机构)?(?:共同|均)?适用|共同适用|均适用|"
            r"(?:社会团体|社团|协会|商会)[、/和及与](?:民办非企业(?:单位)?|民非|社会服务机构)|"
            r"(?:民办非企业(?:单位)?|民非|社会服务机构)[、/和及与](?:社会团体|社团|协会|商会))",
            compact,
        ):
            return "both"
        return "unknown"
    if has_social:
        return "social"
    if has_mfei:
        return "mfei"
    return "unknown"


def _decode_text(raw: bytes, *, normalize_line_endings: bool = True) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            decoded = raw.decode(encoding)
            return _normalize_line_endings(decoded) if normalize_line_endings else decoded
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8", raw, 0, len(raw), "unsupported text encoding")


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _header_key(value: object) -> str:
    return re.sub(r"\s+", "", _cell_text(value)).casefold()


def _find_column(headers: list[str], aliases: frozenset[str]) -> int | None:
    normalized_aliases = {_header_key(alias) for alias in aliases}
    for index, header in enumerate(headers):
        if _header_key(header) in normalized_aliases:
            return index
    return None


def _column_name(index: int) -> str:
    value = index + 1
    letters = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _keywords(value: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                part.strip()
                for part in re.split(r"[,，;；/、|]", value)
                if part.strip()
            }
        )
    )


def _structured_record(
    *,
    source_file: str,
    locator: str,
    values: list[str],
    headers: list[str],
) -> ImportRecord | None:
    question_index = _find_column(headers, _QUESTION_HEADERS)
    answer_index = _find_column(headers, _ANSWER_HEADERS)
    if question_index is None or answer_index is None:
        return None
    question = values[question_index] if question_index < len(values) else ""
    reply = values[answer_index] if answer_index < len(values) else ""
    if not question.strip() or not reply.strip():
        return None

    def optional(aliases: frozenset[str]) -> str:
        index = _find_column(headers, aliases)
        return values[index] if index is not None and index < len(values) else ""

    organization_type = optional(_ORG_HEADERS)
    raw_scope = optional(_SCOPE_HEADERS).strip()
    raw_status = optional(_STATUS_HEADERS).strip()
    source_excerpt = "\t".join(values)
    metadata_scope = (
        _scope_from_verified_excerpt(organization_type)
        if organization_type.strip()
        else "unknown"
    )
    if raw_scope and metadata_scope != "unknown" and raw_scope != metadata_scope:
        raise ValueError("机构范围与机构类型单元格矛盾")
    if not raw_scope:
        inferred_scope = (
            metadata_scope
            if metadata_scope != "unknown"
            else _scope_from_verified_excerpt(source_excerpt)
        )
        if inferred_scope != "unknown":
            raw_scope = inferred_scope
    raw: dict[str, object] = {
        "question": question,
        "reply": reply,
        "source_file": source_file,
        "source_locator": locator,
        "source_excerpt": source_excerpt,
        "organization_type": organization_type,
        "keywords": _keywords(optional(_KEYWORD_HEADERS)),
    }
    if raw_scope:
        raw["org_scope"] = raw_scope
    if raw_status:
        raw["status"] = raw_status
    knowledge = record_from_example(raw)
    return _import_record_from_knowledge(knowledge)


def _import_record_from_knowledge(record: KnowledgeRecord) -> ImportRecord:
    return ImportRecord(
        question=record.question,
        reply=record.reply,
        source_file=record.source_file,
        source_locator=record.source_locator,
        source_excerpt=record.source_excerpt,
        source_hash=record.source_hash,
        org_scope=record.org_scope,
        status=record.status,
        organization_type=record.organization_type,
        keywords=record.keywords,
    )


def _tabular_rows(source: _SourceFile) -> list[tuple[str, list[str]]] | None:
    if source.suffix in {".csv", ".tsv"}:
        delimiter = "\t" if source.suffix == ".tsv" else ","
        reader = csv.reader(
            io.StringIO(
                _decode_text(source.raw, normalize_line_endings=False), newline=""
            ),
            delimiter=delimiter,
        )
        rows = [[_cell_text(value) for value in row] for row in reader]
        if not rows:
            return []
        return [(f"row {index}", row) for index, row in enumerate(rows, 1)]
    if source.suffix == ".xlsx":
        from openpyxl import load_workbook

        workbook = load_workbook(io.BytesIO(source.raw), read_only=True, data_only=True)
        try:
            rows: list[tuple[str, list[str]]] = []
            for sheet in workbook.worksheets:
                for row_index, row in enumerate(sheet.iter_rows(values_only=True), 1):
                    rows.append(
                        (
                            f"{sheet.title}!A{row_index}:{_column_name(max(0, len(row) - 1))}{row_index}",
                            [_cell_text(value) for value in row],
                        )
                    )
            return rows
        finally:
            workbook.close()
    if source.suffix == ".xls":
        import xlrd

        workbook = xlrd.open_workbook(file_contents=source.raw)
        rows = []
        for sheet in workbook.sheets():
            for row_index in range(sheet.nrows):
                values = [_cell_text(sheet.cell_value(row_index, col)) for col in range(sheet.ncols)]
                rows.append(
                    (
                        f"{sheet.name}!A{row_index + 1}:{_column_name(max(0, sheet.ncols - 1))}{row_index + 1}",
                        values,
                    )
                )
        return rows
    return None


def _import_structured(source: _SourceFile) -> tuple[list[ImportRecord], list[str]]:
    rows = _tabular_rows(source)
    if rows is None:
        return [], [f"{source.name}：不支持的结构化格式"]
    if not rows:
        return [], [f"{source.name}：文件为空"]
    _header_locator, headers = rows[0]
    if _find_column(headers, _QUESTION_HEADERS) is None or _find_column(headers, _ANSWER_HEADERS) is None:
        return [], [f"{source.name}：未识别到问题列和答案列"]
    records: list[ImportRecord] = []
    errors: list[str] = []
    for locator, values in rows[1:]:
        try:
            record = _structured_record(
                source_file=source.name,
                locator=locator,
                values=values,
                headers=headers,
            )
            if record is not None:
                records.append(record)
        except (TypeError, ValueError) as error:
            errors.append(f"{source.name} {locator}：{error}")
    if not records and not errors:
        errors.append(f"{source.name}：未找到有效问答记录")
    return records, errors


def _html_text(text: str) -> str:
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    value = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6])\s*>", "\n", value)
    return _normalize_line_endings(html.unescape(re.sub(r"<[^>]+>", "", value)))


def _docx_document(raw: bytes) -> _DocumentText:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", errors="strict")
    paragraphs: list[tuple[str, str]] = []
    for index, paragraph in enumerate(re.findall(r"<w:p\b.*?</w:p>", xml, re.S), 1):
        parts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", paragraph, re.S)
        text = _normalize_line_endings(html.unescape("".join(parts)))
        if text.strip():
            paragraphs.append((f"paragraph {index}", text))
    return _DocumentText("\n".join(text for _locator, text in paragraphs), tuple(paragraphs))


def _pdf_document(source_name: str, raw: bytes) -> _DocumentText:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw))
    pages: list[tuple[str, str]] = []
    errors: list[str] = []
    for index, page in enumerate(reader.pages, 1):
        try:
            text = _normalize_line_endings(str(page.extract_text() or ""))
            pages.append((f"page {index}", text))
        except Exception as error:
            errors.append(f"{source_name} 第{index}页文本提取失败：{type(error).__name__}")
    return _DocumentText("\n".join(text for _locator, text in pages), tuple(pages), tuple(errors))


def _unstructured_document(source: _SourceFile) -> _DocumentText:
    if source.suffix in {".txt", ".md", ".json"}:
        text = _decode_text(source.raw)
        return _DocumentText(text, (("document", text),))
    if source.suffix in {".html", ".htm"}:
        text = _html_text(_decode_text(source.raw))
        return _DocumentText(text, (("document", text),))
    if source.suffix == ".docx":
        return _docx_document(source.raw)
    if source.suffix == ".pdf":
        return _pdf_document(source.name, source.raw)
    raise ValueError(f"{source.name}：不支持的文档格式")


def _model_rows(model_client: ImportModel, text: str, source_file: str) -> list[Mapping[str, object]]:
    result = model_client.extract(text, source_file=source_file)
    if not isinstance(result, list):
        raise ValueError("模型输出必须是问答对象数组")
    rows: list[Mapping[str, object]] = []
    for row in result:
        if not isinstance(row, Mapping):
            raise ValueError("模型输出数组成员必须是对象")
        if set(row) - _MODEL_FIELDS:
            raise ValueError("模型输出包含未批准字段")
        rows.append(row)
    return rows


def _source_locator(document: _DocumentText, excerpt: str) -> str:
    for locator, segment in document.segments:
        if excerpt in segment:
            return locator
    start = document.text.find(excerpt)
    return f"chars {start + 1}-{start + len(excerpt)}" if start >= 0 else ""


def _import_unstructured(
    source: _SourceFile, model_client: ImportModel | None, report: ImportReport
) -> tuple[list[ImportRecord], list[str]]:
    document = _unstructured_document(source)
    errors = list(document.errors)
    if len(document.text) > MAX_MODEL_INPUT_CHARS:
        return [], [*errors, OVER_LIMIT_ERROR]
    if not document.text.strip():
        return [], [*errors, f"{source.name}：未提取到文档文字"]
    if model_client is None:
        return [], [*errors, f"{source.name}：非结构化文档需要配置模型提取器"]
    report.model_calls += 1
    try:
        rows = _model_rows(model_client, document.text, source.name)
    except ImportModelError as error:
        return [], [*errors, f"{source.name}：{error}"]
    except Exception as error:
        return [], [*errors, f"{source.name}：模型提取失败（{type(error).__name__}）"]
    if not rows:
        return [], [*errors, f"{source.name}：未识别到有效问答记录"]

    records: list[ImportRecord] = []
    normalized_document = _normalize_line_endings(document.text)
    for index, row in enumerate(rows, 1):
        question = row.get("question")
        reply = row.get("reply")
        claimed_excerpt = row.get("source_excerpt")
        if not all(isinstance(value, str) and value.strip() for value in (question, reply, claimed_excerpt)):
            errors.append(f"{source.name} 模型记录{index}：问题、答案或来源摘录缺失")
            continue
        excerpt = _normalize_line_endings(claimed_excerpt)
        normalized_reply = _normalize_line_endings(reply)
        excerpt_verified = excerpt in normalized_document
        answer_verified = normalized_reply in excerpt
        verified = excerpt_verified and answer_verified
        locator = _source_locator(document, excerpt) if excerpt_verified else ""
        claimed_scope = row.get("org_scope", "unknown")
        keywords_value = row.get("keywords", ())
        raw_scope = (
            _scope_from_verified_excerpt(excerpt) if excerpt_verified else "unknown"
        )
        organization_type = {
            "social": "社会团体",
            "mfei": "民办非企业单位",
            "both": "社会团体、民办非企业单位",
        }.get(raw_scope, "")
        if (
            isinstance(claimed_scope, str)
            and claimed_scope in _SCOPES
            and claimed_scope != "unknown"
            and claimed_scope != raw_scope
        ):
            report.warnings.append(
                f"{source.name} 模型记录{index}：模型机构范围已忽略，采用来源原文本地判定"
            )
        if isinstance(keywords_value, str):
            parsed_keywords = _keywords(keywords_value)
        elif isinstance(keywords_value, (list, tuple)) and all(
            isinstance(value, str) and value.strip() for value in keywords_value
        ):
            parsed_keywords = tuple(sorted({value.strip() for value in keywords_value}))
        else:
            parsed_keywords = ()
        status: RecordStatus = (
            "active" if verified and raw_scope != "unknown" else "pending"
        )
        if not excerpt_verified:
            report.warnings.append(
                f"{source.name} 模型记录{index}：来源原文无法核对，已转待确认"
            )
        elif not answer_verified:
            report.warnings.append(
                f"{source.name} 模型记录{index}：答案不是来源摘录中的原文，已转待确认"
            )
        elif raw_scope == "unknown":
            report.warnings.append(
                f"{source.name} 模型记录{index}：机构范围不明确，已转待确认"
            )
        try:
            records.append(
                ImportRecord(
                    question=question,
                    reply=normalized_reply,
                    source_file=source.name,
                    source_locator=locator,
                    source_excerpt=excerpt,
                    source_hash=_source_hash(excerpt),
                    org_scope=raw_scope,  # type: ignore[arg-type]
                    status=status,
                    organization_type=organization_type,
                    keywords=parsed_keywords,
                )
            )
        except ValueError as error:
            errors.append(f"{source.name} 模型记录{index}：{error}")
    return records, errors


def _iter_source_files(path: Path) -> Iterable[_SourceFile]:
    if path.is_dir():
        for item in sorted(path.rglob("*"), key=lambda value: value.as_posix().casefold()):
            if item.is_file() and item.suffix.lower() in _SUPPORTED_SUFFIXES:
                yield _SourceFile(item.relative_to(path).as_posix(), item.read_bytes())
        return
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            for name in sorted(archive.namelist(), key=str.casefold):
                if not name.endswith("/") and Path(name).suffix.lower() in _SUPPORTED_SUFFIXES:
                    yield _SourceFile(Path(name).as_posix(), archive.read(name))
        return
    if path.is_file() and path.suffix.lower() in _SUPPORTED_SUFFIXES:
        yield _SourceFile(path.name, path.read_bytes())


def _validate_checkpoint(value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError("import checkpoint root must be an object")
    if set(value) != {
        "schema_version",
        "records",
        "errors",
        "warnings",
        "model_calls",
        "completed_files",
    }:
        raise ValueError("import checkpoint fields mismatch")
    if value["schema_version"] != IMPORT_STAGE_SCHEMA_VERSION:
        raise ValueError("unsupported import checkpoint schema")
    if (
        type(value["records"]) is not list
        or type(value["errors"]) is not list
        or type(value["warnings"]) is not list
    ):
        raise ValueError("invalid import checkpoint lists")
    if type(value["completed_files"]) is not list or type(value["model_calls"]) is not int:
        raise ValueError("invalid import checkpoint progress")


def _checkpoint_path() -> Path:
    override = os.environ.get(IMPORT_STAGE_ENV, "").strip()
    runtime_root = data_dir().resolve()
    canonical_stage = (runtime_root / "knowledge_import_stage.json").resolve()
    checkpoint = (Path(override) if override else canonical_stage).resolve()
    runtime_key = os.path.normcase(str(runtime_root))
    checkpoint_key = os.path.normcase(str(checkpoint))
    canonical_key = os.path.normcase(str(canonical_stage))
    inside_runtime = checkpoint_key == runtime_key or checkpoint_key.startswith(
        runtime_key + os.sep
    )
    if inside_runtime and checkpoint_key != canonical_key:
        raise ImportCheckpointError("导入暂存路径不能指向正式数据或恢复路径")
    return checkpoint


def _validate_checkpoint_source_boundary(checkpoint: Path, source: Path) -> None:
    checkpoint_key = os.path.normcase(str(checkpoint.resolve()))
    source_path = source.resolve()
    source_key = os.path.normcase(str(source_path))
    aliases_source = checkpoint_key == source_key
    if source_path.is_dir():
        aliases_source = aliases_source or checkpoint_key.startswith(source_key + os.sep)
    if aliases_source:
        raise ImportCheckpointError("导入暂存路径不能位于导入来源内")


def _write_checkpoint(report: ImportReport) -> None:
    checkpoint = report.checkpoint_path or _checkpoint_path()
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(checkpoint, report.to_checkpoint(), _validate_checkpoint)
    report.checkpoint_path = checkpoint


def stage_import(path: Path | str, model_client: ImportModel | None = None) -> ImportReport:
    """Stage each source file whole; never split, truncate, or mutate the live repository."""
    source_path = Path(path)
    report = ImportReport(checkpoint_path=_checkpoint_path())
    _validate_checkpoint_source_boundary(report.checkpoint_path, source_path)
    if not source_path.exists():
        report.errors.append(f"导入路径不存在：{source_path}")
        _write_checkpoint(report)
        return report
    found = False
    sources = iter(_iter_source_files(source_path))
    while True:
        try:
            source = next(sources)
        except StopIteration:
            break
        except Exception as error:
            report.errors.append(f"读取导入容器失败（{type(error).__name__}）")
            _write_checkpoint(report)
            return report
        found = True
        try:
            if source.suffix in _STRUCTURED_SUFFIXES:
                records, errors = _import_structured(source)
            else:
                records, errors = _import_unstructured(source, model_client, report)
            report.records.extend(records)
            report.errors.extend(errors)
        except Exception as error:
            report.errors.append(
                f"{source.name}：导入失败（{type(error).__name__}）"
            )
        report.completed_files.append(source.name)
        _write_checkpoint(report)
    if not found:
        report.errors.append(
            "未找到支持的知识库文件（支持 PDF、DOCX、XLSX/XLS、CSV/TSV、"
            "TXT/MD/JSON/HTML 和 ZIP）"
        )
        _write_checkpoint(report)
    return report


def activate_import(report: ImportReport, repository: object) -> KnowledgeAudit:
    """Audit the complete replacement before making exactly one repository call."""
    if not isinstance(report, ImportReport):
        raise ImportActivationError("无效的导入暂存报告")
    if report.errors:
        details = "；".join(report.errors)
        raise ImportActivationError(
            f"导入暂存包含错误，正式知识库保持不变：{details}"
        )
    incoming = tuple(record.to_knowledge_record() for record in report.records)
    current_loader = getattr(repository, "current_records", None)
    current = tuple(current_loader()) if callable(current_loader) else ()
    audit = audit_records((*current, *incoming))
    replacement = getattr(repository, "replace_records_atomic", None)
    if not callable(replacement):
        raise ImportActivationError("知识库不支持原子替换")
    replacement(audit)
    return audit
