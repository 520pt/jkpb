from __future__ import annotations

import os
import re
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


BG = "#f2f6fb"
INK = "#1f2937"
LINE = "#dbeafe"
BLUE = "#2d60e6"
CYAN = "#19a9d5"
CARD = "#ffffff"

TEMPLATE_QUERY_TYPES = {
    "tunnel_mechanical_template",
    "tunnel_mechanical_modify_template",
    "patrol_record_template",
    "template",
    "binding",
    "binding_update",
}


def render_wechat_query_image(result: dict[str, Any]) -> bytes | None:
    query_type = str(result.get("query_type") or "").strip()
    if not query_type or query_type in TEMPLATE_QUERY_TYPES or "template" in query_type:
        return None
    title = _title_for_result(result)
    reply = str(result.get("reply") or "").strip()
    if not reply:
        return None

    if query_type == "reminder_all":
        return _render_table(title, query_type, ["人员", "排班", "提醒"], _parse_reminder_all(reply), [132, 234, 526], content_cols={2}, min_h=520)
    if query_type in {"monitor_all"}:
        return _render_table(title, query_type, ["日期", "星期", "早班", "中班", "晚班"], _parse_monitor_all(reply), [180, 100, 220, 220, 220], nowrap_cols={0}, min_h=400)
    if query_type in {"monitor_all_range"}:
        return _render_table(title, query_type, ["日期", "星期", "早班", "中班", "晚班"], _parse_monitor_range(reply), [180, 100, 210, 210, 210], nowrap_cols={0}, min_h=740)
    if query_type in {"monitor"}:
        return _render_table(title, query_type, ["日期", "星期", "班次"], _parse_person_monitor(reply), [200, 120, 520], nowrap_cols={0}, min_h=380)
    if query_type in {"monitor_range"}:
        return _render_table(title, query_type, ["日期", "星期", "班次"], _parse_person_monitor_range(reply), [190, 110, 560], nowrap_cols={0}, min_h=520)
    if query_type in {"reminder"}:
        return _render_table(title, query_type, ["时间", "类型", "内容"], _parse_person_reminder(reply), [140, 140, 600], content_cols={2}, min_h=430)
    if query_type in {"reminder_range"}:
        return _render_table(title, query_type, ["日期", "星期", "排班", "提醒"], _parse_person_reminder_range(reply), [180, 95, 180, 425], content_cols={3}, nowrap_cols={0}, min_h=620)
    if query_type in {"reminder_all_range"}:
        return _render_table(title, query_type, ["日期", "星期", "提醒"], _parse_all_reminder_range(reply), [180, 95, 605], content_cols={2}, nowrap_cols={0}, min_h=620)
    if query_type in {"next_reminder", "next_reminder_all"}:
        return _render_table(title, query_type, ["日期", "人员", "内容"], _parse_next_reminder(reply), [180, 145, 565], content_cols={2}, nowrap_cols={0}, min_h=520)
    if query_type == "help":
        return _render_help_image()
    return _render_message_card(title, query_type, reply)


def _render_table(
    title: str,
    query_type: str,
    headers: list[str],
    rows: list[list[str]],
    widths: list[int],
    *,
    content_cols: set[int] | None = None,
    nowrap_cols: set[int] | None = None,
    min_h: int = 420,
) -> bytes:
    content_cols = content_cols or set()
    nowrap_cols = nowrap_cols or set()
    width = sum(widths) + 88
    table_x = 44
    header_y = 44
    header_h = 120
    table_y = 182
    th = 43
    fonts = {"title": _font(34), "sub": _font(22), "head": _font(20), "body": _font(22)}
    dummy = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    wrapped: list[list[list[str]]] = []
    heights: list[int] = []
    for row in rows or [["暂无", "", ""][: len(headers)]]:
        line_cells: list[list[str]] = []
        max_lines = 1
        for index, cell in enumerate(row):
            text = str(cell)
            if index in nowrap_cols:
                lines = [text]
            elif index in content_cols:
                lines = _wrap_text(dummy, text, fonts["body"], widths[index] - 24)
            else:
                lines = [text]
            max_lines = max(max_lines, len(lines))
            line_cells.append(lines)
        wrapped.append(line_cells)
        heights.append(max(54, max_lines * 28 + 28))
    height = max(min_h, table_y + th + 8 + sum(heights) + max(0, len(heights) - 1) * 8 + 56)
    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)
    _rounded(draw, (24, 25, width - 24, height - 24), 30, CARD, "#cbd5e1")
    _rounded(draw, (table_x, header_y, width - 44, header_y + header_h), 20, BLUE)
    draw.text((70, 58), title, font=fonts["title"], fill="#ffffff")
    draw.text((70, 107), query_type, font=fonts["sub"], fill="#ffffff")
    _rounded(draw, (table_x, table_y, width - 44, table_y + th), 14, CYAN)
    x = table_x
    for index, header in enumerate(headers):
        draw.text((x + 12, table_y + 8), header, font=fonts["head"], fill="#ffffff")
        x += widths[index]
        if index < len(headers) - 1:
            draw.line((x, table_y + 6, x, table_y + th - 6), fill="#e6faff", width=2)
    y = table_y + th + 8
    for row_index, line_cells in enumerate(wrapped):
        rh = heights[row_index]
        fill = "#f8fbff" if row_index % 2 == 0 else "#ffffff"
        _rounded(draw, (table_x, y, width - 44, y + rh), 13, fill, LINE)
        x = table_x
        for col_index, lines in enumerate(line_cells):
            if col_index > 0:
                draw.line((x, y, x, y + rh), fill="#dae4f2", width=1)
            text_y = y + max(10, (rh - len(lines) * 28) // 2)
            for line in lines:
                draw.text((x + 12, text_y), line, font=fonts["body"], fill=INK)
                text_y += 28
            x += widths[col_index]
        y += rh + 8
    return _png_bytes(image)


def _render_message_card(title: str, query_type: str, reply: str) -> bytes:
    font_body = _font(22)
    dummy = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    lines: list[str] = []
    for part in reply.splitlines() or [reply]:
        lines.extend(_wrap_text(dummy, part, font_body, 800))
    height = max(420, 182 + 44 + len(lines) * 32 + 72)
    image = Image.new("RGB", (900, height), BG)
    draw = ImageDraw.Draw(image)
    _rounded(draw, (24, 25, 876, height - 24), 30, CARD, "#cbd5e1")
    _rounded(draw, (44, 44, 856, 164), 20, BLUE)
    draw.text((70, 58), title, font=_font(34), fill="#ffffff")
    draw.text((70, 107), query_type, font=_font(22), fill="#ffffff")
    box = (56, 192, 844, height - 52)
    _rounded(draw, box, 18, "#f8fafc", LINE)
    _draw_lines(draw, lines, box[0] + 24, box[1] + 24, font_body, INK, 32)
    return _png_bytes(image)


def _render_help_image() -> bytes:
    commands = [
        "查询我的监控", "查询今日提醒", "查询明日监控", "查询本周监控", "查询未来7天",
        "查询下次提醒", "查询我的绑定", "查询今日机电", "查询2026-08-09机电", "隧道机电",
    ]
    rows = [[str(index + 1), command] for index, command in enumerate(commands)]
    return _render_table("监控查询菜单", "help", ["序号", "功能"], rows, [110, 660], min_h=620)


def _parse_reminder_all(reply: str) -> list[list[str]]:
    rows = []
    for line in reply.splitlines()[1:]:
        if not line.startswith("- "):
            continue
        body = line[2:]
        name, rest = _split_once(body, "：")
        shift, reminder = _split_once(rest, "；")
        rows.append([name, shift, reminder])
    return rows


def _parse_monitor_all(reply: str) -> list[list[str]]:
    lines = reply.splitlines()
    date, weekday = _date_week_from_text(lines[0] if lines else "")
    shifts = {"早班": "", "中班": "", "晚班": ""}
    for line in lines[1:]:
        for key in shifts:
            if line.startswith(f"{key}："):
                shifts[key] = line.split("：", 1)[1]
    return [[date, weekday, shifts["早班"], shifts["中班"], shifts["晚班"]]]


def _parse_monitor_range(reply: str) -> list[list[str]]:
    rows = []
    lines = reply.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("- "):
            continue
        date, weekday = _date_week_from_text(line[2:])
        shifts = {"早班": "", "中班": "", "晚班": ""}
        for child in lines[index + 1 : index + 4]:
            text = child.strip()
            for key in shifts:
                if text.startswith(f"{key}："):
                    shifts[key] = text.split("：", 1)[1]
        rows.append([date, weekday, shifts["早班"], shifts["中班"], shifts["晚班"]])
    return rows


def _parse_person_monitor(reply: str) -> list[list[str]]:
    lines = reply.splitlines()
    date, weekday = _date_week_from_text(lines[0] if lines else "")
    shift = ""
    for line in lines[1:]:
        if line.startswith("排班："):
            shift = line.split("：", 1)[1]
    return [[date, weekday, shift]]


def _parse_person_monitor_range(reply: str) -> list[list[str]]:
    rows = []
    for line in reply.splitlines()[1:]:
        if not line.startswith("- "):
            continue
        left, shift = _split_once(line[2:], "：")
        date, weekday = _date_week_from_text(left)
        rows.append([date, weekday, shift])
    return rows


def _parse_person_reminder(reply: str) -> list[list[str]]:
    rows = []
    for line in reply.splitlines():
        if not line.startswith("- "):
            continue
        body = line[2:]
        time_part, rest = _split_once(body, " ")
        label, content = _split_once(rest, "：")
        rows.append([time_part, label, content])
    return rows


def _parse_person_reminder_range(reply: str) -> list[list[str]]:
    rows = []
    for line in reply.splitlines()[2:]:
        if not line.startswith("- "):
            continue
        left, rest = _split_once(line[2:], "：")
        roster, reminders = _split_once(rest, "；")
        date, weekday = _date_week_from_text(left)
        rows.append([date, weekday, roster, reminders])
    return rows


def _parse_all_reminder_range(reply: str) -> list[list[str]]:
    rows = []
    for line in reply.splitlines()[1:]:
        if not line.startswith("- "):
            continue
        left, reminders = _split_once(line[2:], "：")
        date, weekday = _date_week_from_text(left)
        rows.append([date, weekday, reminders])
    return rows


def _parse_next_reminder(reply: str) -> list[list[str]]:
    rows = []
    default_person = _split_once(reply.splitlines()[0] if reply.splitlines() else "", " ")[0]
    for line in reply.splitlines()[1:]:
        if not line.startswith("- "):
            continue
        body = line[2:]
        if body.startswith("另有"):
            rows.append(["-", "更多", body])
            continue
        match = re.match(r"(\d{4}-\d{2}-\d{2})\s+([^：]+)：(.+)", body)
        if match:
            rows.append([match.group(1), match.group(2), match.group(3)])
            continue
        match = re.match(r"(\d{4}-\d{2}-\d{2})：(.+)", body)
        if match:
            rows.append([match.group(1), default_person, match.group(2)])
    return rows


def _title_for_result(result: dict[str, Any]) -> str:
    query_type = str(result.get("query_type") or "")
    mapping = {
        "help": "帮助",
        "unbound": "未绑定",
        "binding": "我的绑定",
        "reminder_all": "查询今日提醒",
        "reminder": "查询个人提醒",
        "monitor_all": "查询监控",
        "monitor": "查询个人监控",
        "monitor_all_range": "查询未来排班",
        "monitor_range": "查询个人排班",
        "reminder_range": "查询个人提醒",
        "reminder_all_range": "查询提醒汇总",
        "next_reminder_all": "查询下次提醒",
        "next_reminder": "查询下次提醒",
    }
    return mapping.get(query_type, "查询结果")


def _date_week_from_text(text: str) -> tuple[str, str]:
    match = re.search(r"(\d{4}-\d{2}-\d{2})\s+(周.)", text)
    if match:
        return match.group(1), match.group(2)
    return "", ""


def _split_once(value: str, sep: str) -> tuple[str, str]:
    if sep in value:
        left, right = value.split(sep, 1)
        return left, right
    return value, ""


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    if _text_width(draw, text, font) <= max_width:
        return [text]
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = f"{current}{char}"
        if current and _text_width(draw, candidate, font) > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _draw_lines(draw: ImageDraw.ImageDraw, lines: list[str], x: int, y: int, font: ImageFont.ImageFont, fill: str, line_height: int) -> None:
    for index, line in enumerate(lines):
        draw.text((x, y + index * line_height), line, font=font, fill=fill)


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), str(text), font=font)
    return box[2] - box[0]


def _rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int, fill: str, outline: str | None = None) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline)


def _png_bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _candidate_font_paths():
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default()


@lru_cache(maxsize=1)
def _candidate_font_paths() -> list[Path]:
    paths: list[Path] = []
    env_font = os.getenv("CJK_FONT_PATH", "").strip()
    if env_font:
        paths.append(Path(env_font))
    for font_dir in (Path("fonts"), Path("app/static/fonts"), Path("/app/fonts"), Path("/app/app/static/fonts"), Path("C:/Windows/Fonts"), Path("/usr/share/fonts/opentype/noto"), Path("/usr/share/fonts/truetype/noto"), Path("/usr/share/fonts/truetype/wqy")):
        for name in ("NotoSansCJKsc-Regular.otf", "NotoSansSC-Regular.otf", "NotoSansCJK-Regular.ttc", "msyh.ttc", "simhei.ttf", "wqy-microhei.ttc", "WenQuanYi Micro Hei.ttf"):
            paths.append(font_dir / name)
    return paths
