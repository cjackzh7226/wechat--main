"""Parser for complex, merged-cell enterprise annual-report knowledge bases.

The module deliberately uses only the Python standard library so the same
workbook can be imported from a development checkout or a packaged release.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as ET


ENTERPRISE_ANNUAL_REPORT_SOURCE = "enterprise_annual_report_kb"
_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_QUESTION_ANSWER_RE = re.compile(
    r"^\s*(?:问题(?:[\s一二三四五六七八九十百千万零〇0-9]+)?|问)\s*[:：]\s*"
    r"(?P<question>.+?)\s*(?:\r?\n\s*)?(?:答(?:案)?|回答)\s*[:：]\s*"
    r"(?P<answer>.+?)\s*$",
    re.S,
)


@dataclass(frozen=True)
class _MergedRange:
    min_col: int
    min_row: int
    max_col: int
    max_row: int
    reference: str
    origin: str

    def contains(self, column: int, row: int) -> bool:
        return self.min_col <= column <= self.max_col and self.min_row <= row <= self.max_row


@dataclass
class _Sheet:
    name: str
    cells: dict[str, str]
    merges: list[_MergedRange]

    def value(self, column: int, row: int) -> str:
        address = _address(column, row)
        direct = self.cells.get(address, "").strip()
        if direct:
            return direct
        merged = self.merged_range(column, row)
        if merged:
            return self.cells.get(merged.origin, "").strip()
        return ""

    def merged_range(self, column: int, row: int) -> _MergedRange | None:
        for merged in self.merges:
            if merged.contains(column, row):
                return merged
        return None

    @property
    def max_row(self) -> int:
        rows = [_split_address(address)[1] for address in self.cells]
        return max(rows, default=0)

    @property
    def max_col(self) -> int:
        columns = [_split_address(address)[0] for address in self.cells]
        return max(columns, default=0)


def detect_enterprise_annual_report_workbook(name: str, raw: bytes) -> bool:
    """Return true only for the supported annual-report workbook structure."""

    if Path(name).suffix.lower() != ".xlsx":
        return False
    try:
        sheets = _read_xlsx_sheets(raw)
    except (ET.ParseError, KeyError, OSError, ValueError, zipfile.BadZipFile):
        return False
    return any(_looks_like_enterprise_annual_report(sheet) for sheet in sheets)


def parse_enterprise_annual_report_workbook(
    name: str,
    raw: bytes,
    source: str = ENTERPRISE_ANNUAL_REPORT_SOURCE,
) -> list[dict[str, str]]:
    """Extract official Q&A records from a recognized annual-report workbook."""

    if Path(name).suffix.lower() != ".xlsx":
        return []
    try:
        sheets = _read_xlsx_sheets(raw)
    except (ET.ParseError, KeyError, OSError, ValueError, zipfile.BadZipFile):
        return []

    examples: list[dict[str, str]] = []
    for sheet in sheets:
        if not _looks_like_enterprise_annual_report(sheet):
            continue
        examples.extend(_extract_sheet_examples(sheet, Path(name).name, source))
    return examples


def _looks_like_enterprise_annual_report(sheet: _Sheet) -> bool:
    header_values = [_normalise_label(sheet.value(column, 1)) for column in range(1, sheet.max_col + 1)]
    has_module = "模块" in header_values
    organization_columns = _organization_columns(sheet)
    qa_count = sum(
        1
        for row in range(2, sheet.max_row + 1)
        for column, _label in organization_columns
        if _split_question_answer(sheet.value(column, row))
    )
    return has_module and len(organization_columns) >= 1 and qa_count >= 2


def _organization_columns(sheet: _Sheet) -> list[tuple[int, str]]:
    columns: list[tuple[int, str]] = []
    for column in range(1, sheet.max_col + 1):
        label = sheet.value(column, 1).strip()
        normalized = _normalise_label(label)
        if normalized in {"社会团体", "民办非企业单位", "社会服务机构"}:
            columns.append((column, label))
    return columns


def _extract_sheet_examples(sheet: _Sheet, file_name: str, source: str) -> list[dict[str, str]]:
    module_column = _find_header_column(sheet, "模块")
    if not module_column:
        return []
    subcategory_column = module_column + 1
    examples: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()

    for row in range(2, sheet.max_row + 1):
        module = sheet.value(module_column, row)
        subcategory = sheet.value(subcategory_column, row)
        for column, organization_type in _organization_columns(sheet):
            cell_address = _address(column, row)
            merged = sheet.merged_range(column, row)
            if merged and cell_address != merged.origin:
                continue
            source_cell = merged.reference if merged else cell_address
            parsed = _split_question_answer(sheet.value(column, row))
            if not parsed:
                continue
            question, answer = parsed
            applicable_organizations = (
                [
                    label
                    for organization_column, label in _organization_columns(sheet)
                    if merged and merged.contains(organization_column, row)
                ]
                if merged
                else [organization_type]
            )
            organization_types = "、".join(applicable_organizations)
            key = (organization_types, question, answer, source_cell)
            if key in seen:
                continue
            seen.add(key)
            cue_parts = ["企业年报", organization_types, module, subcategory, question]
            cue = " / ".join(part.strip() for part in cue_parts if part and part.strip())
            examples.append(
                {
                    "partner": "企业年报知识库",
                    "cue": cue,
                    "question": question,
                    "reply": answer,
                    "source": source,
                    "module": module,
                    "subcategory": subcategory,
                    "organization_type": organization_types,
                    "source_sheet": sheet.name,
                    "source_cell": source_cell,
                    "source_file": file_name,
                }
            )
    return examples


def _find_header_column(sheet: _Sheet, label: str) -> int:
    normalized = _normalise_label(label)
    for column in range(1, sheet.max_col + 1):
        if _normalise_label(sheet.value(column, 1)) == normalized:
            return column
    return 0


def _split_question_answer(value: str) -> tuple[str, str] | None:
    match = _QUESTION_ANSWER_RE.match((value or "").strip())
    if not match:
        return None
    question = _clean_text(match.group("question"))
    answer = _clean_text(match.group("answer"))
    if not question or not answer:
        return None
    return question, answer


def _clean_text(value: str) -> str:
    return "\n".join(line.strip() for line in (value or "").splitlines() if line.strip()).strip()


def _normalise_label(value: str) -> str:
    return re.sub(r"[\s:：()（）【】\[\]、，,。.!！?？]+", "", value or "").lower()


def _read_xlsx_sheets(raw: bytes) -> list[_Sheet]:
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        shared_strings = _read_shared_strings(archive)
        sheet_paths = _read_sheet_paths(archive)
        sheets: list[_Sheet] = []
        for name, path in sheet_paths:
            if path not in archive.namelist():
                continue
            sheets.append(_read_sheet(name, archive.read(path), shared_strings))
        if sheets:
            return sheets
        fallback_paths = sorted(
            item for item in archive.namelist() if item.startswith("xl/worksheets/") and item.endswith(".xml")
        )
        return [_read_sheet(Path(path).stem, archive.read(path), shared_strings) for path in fallback_paths]


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return [_text_content(item) for item in root.findall(f"{{{_MAIN_NS}}}si")]


def _read_sheet_paths(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook_path = "xl/workbook.xml"
    rels_path = "xl/_rels/workbook.xml.rels"
    if workbook_path not in archive.namelist() or rels_path not in archive.namelist():
        return []
    relationships = ET.fromstring(archive.read(rels_path))
    targets = {
        relation.attrib.get("Id", ""): relation.attrib.get("Target", "")
        for relation in relationships.findall(f"{{{_PACKAGE_REL_NS}}}Relationship")
    }
    workbook = ET.fromstring(archive.read(workbook_path))
    result: list[tuple[str, str]] = []
    for sheet in workbook.findall(f".//{{{_MAIN_NS}}}sheet"):
        relation_id = sheet.attrib.get(f"{{{_REL_NS}}}id", "")
        target = targets.get(relation_id, "")
        if not target:
            continue
        normalized_target = target.replace("\\", "/").lstrip("/")
        path = normalized_target if normalized_target.startswith("xl/") else f"xl/{normalized_target}"
        result.append((sheet.attrib.get("name", Path(path).stem), path))
    return result


def _read_sheet(name: str, raw: bytes, shared_strings: list[str]) -> _Sheet:
    root = ET.fromstring(raw)
    cells: dict[str, str] = {}
    for cell in root.findall(f".//{{{_MAIN_NS}}}c"):
        address = cell.attrib.get("r", "")
        if not address:
            continue
        cells[address] = _cell_value(cell, shared_strings)
    merges: list[_MergedRange] = []
    for merged in root.findall(f".//{{{_MAIN_NS}}}mergeCell"):
        parsed = _parse_range(merged.attrib.get("ref", ""))
        if parsed:
            merges.append(parsed)
    return _Sheet(name=name, cells=cells, merges=merges)


def _cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return _text_content(cell)
    value = cell.findtext(f"{{{_MAIN_NS}}}v", default="")
    if cell_type == "s":
        try:
            return shared_strings[int(value)]
        except (IndexError, TypeError, ValueError):
            return ""
    return value or ""


def _text_content(element: ET.Element) -> str:
    return "".join(node.text or "" for node in element.iter() if node.tag == f"{{{_MAIN_NS}}}t")


def _parse_range(value: str) -> _MergedRange | None:
    parts = value.split(":", 1)
    try:
        min_col, min_row = _split_address(parts[0])
        max_col, max_row = _split_address(parts[-1])
    except ValueError:
        return None
    return _MergedRange(min_col, min_row, max_col, max_row, value, _address(min_col, min_row))


def _split_address(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"([A-Za-z]+)(\d+)", value or "")
    if not match:
        raise ValueError(f"Invalid cell address: {value!r}")
    letters, row = match.groups()
    column = 0
    for letter in letters.upper():
        column = column * 26 + ord(letter) - ord("A") + 1
    return column, int(row)


def _address(column: int, row: int) -> str:
    letters: list[str] = []
    while column:
        column, remainder = divmod(column - 1, 26)
        letters.append(chr(ord("A") + remainder))
    return f"{''.join(reversed(letters))}{row}"
