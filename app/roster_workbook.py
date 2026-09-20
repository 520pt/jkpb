from __future__ import annotations

import calendar
import posixpath
import re
import zipfile
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
CELL_RE = re.compile(r"^([A-Z]+)(\d+)$")
MONTH_RE = re.compile("(\\d{1,2})\\s*\u6708")
YEAR_RE = re.compile("(20\\d{2})\\s*\u5e74")
STAT_HEADER_WORDS = {
    "\u5de5\u4f5c\u65e5\u5929\u6570",
    "\u8f6e\u4f11\u5929\u6570",
    "\u8282\u5047\u65e5\u52a0\u73ed\u5929\u6570",
    "\u8282\u5047\u65e5\u672a\u52a0\u73ed\u5929\u6570",
    "\u672a\u4f11\u5929\u6570",
}
VALID_SHIFT_CODES = {
    "\u65e9",
    "\u4e2d",
    "\u665a",
    "\u4f11",
    "\u51fa\u5dee",
    "\u5de1",
    "\u5907",
    "\u529e",
}


def extract_roster_workbook(path: str | Path, *, default_year: int | None = None) -> dict[str, Any]:
    source = Path(path)
    months: list[dict[str, Any]] = []
    with zipfile.ZipFile(source) as archive:
        shared_strings = _read_shared_strings(archive)
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = _read_workbook_relationships(archive)
        workbook_year = _year_from_text(source.name) or default_year
        for sheet in workbook.findall("m:sheets/m:sheet", NS):
            if sheet.attrib.get("state", "visible") != "visible":
                continue
            sheet_name = sheet.attrib.get("name", "")
            month = _month_from_sheet_name(sheet_name)
            if not month:
                continue
            rel_id = sheet.attrib.get(f"{{{NS['r']}}}id", "")
            target_name = _sheet_target_path(rels.get(rel_id, ""))
            if not target_name or target_name not in archive.namelist():
                continue
            cells = _read_cells(archive, target_name, shared_strings)
            parsed = _parse_month_sheet(cells, sheet_name=sheet_name, month=month, default_year=workbook_year)
            if parsed["grid"]:
                parsed["source_image_path"] = str(source)
                months.append(parsed)
    if not months:
        raise ValueError("\u6ca1\u6709\u5728\u5de5\u4f5c\u7c3f\u91cc\u627e\u5230\u53ef\u5bfc\u5165\u7684\u6708\u4efd\u6392\u73ed")
    months.sort(key=lambda item: (int(item["year"]), int(item["month"])))
    return {"source_file_path": str(source), "workbook_status": "ok", "months": months}


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall("m:si", NS):
        values.append("".join(text.text or "" for text in item.findall(".//m:t", NS)))
    return values


def _read_workbook_relationships(archive: zipfile.ZipFile) -> dict[str, str]:
    root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    return {rel.attrib.get("Id", ""): rel.attrib.get("Target", "") for rel in root.findall("rel:Relationship", NS)}


def _sheet_target_path(target: str) -> str:
    if not target:
        return ""
    if target.startswith("/"):
        return target.lstrip("/")
    if target.startswith("xl/"):
        return target
    return posixpath.normpath(posixpath.join("xl", target))


def _read_cells(archive: zipfile.ZipFile, target_name: str, shared_strings: list[str]) -> dict[tuple[int, int], str]:
    root = ET.fromstring(archive.read(target_name))
    cells: dict[tuple[int, int], str] = {}
    for cell in root.findall(".//m:c", NS):
        ref = cell.attrib.get("r", "")
        position = _cell_position(ref)
        if not position:
            continue
        value = _cell_text(cell, shared_strings)
        if value != "":
            cells[position] = value
    return cells


def _cell_text(cell: ET.Element, shared_strings: list[str]) -> str:
    inline = cell.find("m:is", NS)
    if inline is not None:
        return _clean_cell_text("".join(text.text or "" for text in inline.findall(".//m:t", NS)))
    value = cell.find("m:v", NS)
    raw = value.text if value is not None and value.text is not None else ""
    if cell.attrib.get("t") == "s" and raw:
        try:
            return _clean_cell_text(shared_strings[int(raw)])
        except (ValueError, IndexError):
            return ""
    return _clean_cell_text(raw)


def _cell_position(ref: str) -> tuple[int, int] | None:
    match = CELL_RE.match(ref or "")
    if not match:
        return None
    return int(match.group(2)), _column_number(match.group(1))


def _column_number(letters: str) -> int:
    value = 0
    for char in letters:
        value = value * 26 + ord(char) - ord("A") + 1
    return value


def _parse_month_sheet(cells: dict[tuple[int, int], str], *, sheet_name: str, month: int, default_year: int | None) -> dict[str, Any]:
    title_text = " ".join(value for (row, _), value in cells.items() if row <= 4)
    year = _year_from_text(title_text) or default_year or 2026
    day_columns = _day_columns(cells, year, month)
    grid: list[dict[str, Any]] = []
    if day_columns:
        for row_index in sorted({row for row, _ in cells if row >= 4}):
            name = _clean_cell_text(cells.get((row_index, 3), ""))
            if not name:
                continue
            days: dict[str, str] = {}
            for day, column in day_columns:
                value = _normalize_shift_value(cells.get((row_index, column), ""))
                if value:
                    days[str(day)] = value
            if days:
                grid.append({"name": name, "days": days})
    return {"year": int(year), "month": int(month), "sheet_name": sheet_name.strip(), "grid": grid}


def _day_columns(cells: dict[tuple[int, int], str], year: int, month: int) -> list[tuple[int, int]]:
    max_day = calendar.monthrange(int(year), int(month))[1]
    columns: list[tuple[int, int]] = []
    expected = 1
    for column in range(4, 200):
        text = _clean_cell_text(cells.get((2, column), ""))
        if not text:
            if columns:
                break
            continue
        if text in STAT_HEADER_WORDS or not text.isdigit():
            if columns:
                break
            continue
        day = int(text)
        if day != expected or day > max_day:
            if columns:
                break
            continue
        columns.append((day, column))
        expected += 1
    return columns


def _normalize_shift_value(value: str) -> str:
    text = _clean_cell_text(value)
    if not text:
        return ""
    text = text.replace(" ", "")
    if text == "\u51fa\u5dee":
        return "\u51fa\u5dee"
    return text if text in VALID_SHIFT_CODES else text


def _clean_cell_text(value: Any) -> str:
    return re.sub(r"[ \t\r\n]+", "", str(value or "").strip())


def _month_from_sheet_name(value: str) -> int | None:
    match = MONTH_RE.search(str(value or ""))
    if not match:
        return None
    month = int(match.group(1))
    return month if 1 <= month <= 12 else None


def _year_from_text(value: str) -> int | None:
    match = YEAR_RE.search(str(value or ""))
    if not match:
        return None
    return int(match.group(1))
