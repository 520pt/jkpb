from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any
import re

from PIL import Image, ImageDraw, ImageFont

from app.daily_duty_image import _font
from app.patrol_warning import _patrol_record_person_signature


WIDTH = 1280
LEFT = 28
PAIR_WINDOW_SECONDS = 60 * 60


def render_patrol_record_image(
    records: list[dict[str, Any]],
    *,
    name: str,
    start_date: str,
    end_date: str,
    route_code: str = "",
) -> bytes:
    groups = _record_groups(records)
    fonts = {
        "title": _font(28, bold=True),
        "summary": _font(20),
        "header": _font(19, bold=True),
        "body": _font(18),
        "count": _font(20, bold=True),
    }
    columns = [
        ("次数", 76),
        ("日期", 150),
        ("时间", 190),
        ("方向", 100),
        ("巡查人", 350),
        ("记录人", 350),
    ]
    table_width = sum(width for _, width in columns)
    row_padding_y = 13
    row_line_height = 28
    header_height = 58
    title_height = 124
    row_layouts: list[dict[str, Any]] = []
    for group in groups:
        row_heights = []
        for record in group["records"]:
            values = _record_values(record)
            line_counts = [
                len(_wrap_text(values[index], width - 24, fonts["body"], preserve_name_groups=index >= 3))
                for index, (_, width) in enumerate(columns[1:])
            ]
            row_heights.append(max(52, max(line_counts, default=1) * row_line_height + row_padding_y * 2))
        row_layouts.append({"group": group, "row_heights": row_heights, "height": sum(row_heights)})

    table_height = header_height + sum(item["height"] for item in row_layouts)
    height = title_height + table_height + 28
    image = Image.new("RGB", (WIDTH, height), "#f6f8fb")
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((LEFT, 18, WIDTH - LEFT, 82), radius=8, fill="#172033")
    draw.text((LEFT + 20, 34), "巡查记录统计", font=fonts["title"], fill="#ffffff")
    query_text = f"姓名：{name}    日期：{start_date} 至 {end_date}"
    if route_code:
        query_text += f"    路线：{route_code}"
    draw.text((LEFT + 20, 95), query_text, font=fonts["summary"], fill="#475569")
    draw.text(
        (WIDTH - LEFT - 360, 95),
        f"巡查记录：{len(records)}条  实际次数：{len(groups)}次",
        font=fonts["summary"],
        fill="#475569",
    )

    table_x = LEFT
    table_y = title_height
    draw.rectangle((table_x, table_y, table_x + table_width, table_y + header_height), fill="#f8fafc")
    x = table_x
    for title, width in columns:
        _draw_centered(draw, (x, table_y, x + width, table_y + header_height), title, fonts["header"], "#111827")
        x += width
    _draw_grid(draw, table_x, table_y, table_width, header_height, [width for _, width in columns], "#cbd5e1")

    y = table_y + header_height
    for layout_index, layout in enumerate(row_layouts):
        group = layout["group"]
        highlighted = len(group["records"]) > 1 or group["records"][0].get("direction") == "双向"
        group_y = y
        group_height = layout["height"]
        draw.rectangle((table_x, group_y, table_x + columns[0][1], group_y + group_height), fill="#ffffff")
        _draw_centered(draw, (table_x, group_y, table_x + columns[0][1], group_y + group_height), str(group["count"]), fonts["count"], "#111827")
        row_y = group_y
        for row_index, record in enumerate(group["records"]):
            values = _record_values(record)
            row_height = layout["row_heights"][row_index]
            cell_x = table_x + columns[0][1]
            for value_index, value in enumerate(values):
                width = columns[value_index + 1][1]
                draw.rectangle((cell_x, row_y, cell_x + width, row_y + row_height), fill="#ffffff")
                _draw_wrapped_centered(
                    draw,
                    (cell_x, row_y, cell_x + width, row_y + row_height),
                    value,
                    fonts["body"],
                    preserve_name_groups=value_index >= 3,
                )
                cell_x += width
            row_y += row_height
        _draw_grid(
            draw,
            table_x,
            group_y,
            table_width,
            group_height,
            [width for _, width in columns],
            "#cbd5e1",
            row_heights=layout["row_heights"],
        )
        if highlighted:
            _draw_group_border(draw, table_x, group_y, table_width, group_height, skip_top=layout_index > 0 and _is_highlighted(row_layouts[layout_index - 1]["group"]))
        y += group_height

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _record_groups(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(records or [], key=lambda record: (str(record.get("start_time") or ""), str(record.get("id") or "")))
    groups: list[dict[str, Any]] = []
    index = 0
    while index < len(ordered):
        group_records = [ordered[index]]
        index += 1
        while index < len(ordered) and _can_join(group_records[-1], ordered[index]):
            group_records.append(ordered[index])
            index += 1
        groups.append({"count": len(groups) + 1, "records": group_records})
    return groups


def _can_join(current: dict[str, Any], following: dict[str, Any] | None) -> bool:
    if not following:
        return False
    if str(current.get("route_code") or "").strip().upper() != str(following.get("route_code") or "").strip().upper():
        return False
    if str(current.get("direction") or "") not in {"上行", "下行"}:
        return False
    if str(following.get("direction") or "") not in {"上行", "下行"}:
        return False
    if not str(current.get("end_time") or "").strip() and _patrol_record_person_signature(current) != _patrol_record_person_signature(following):
        return False
    current_start = _record_datetime(current, "start_time", "end_time")
    current_end = _record_datetime(current, "end_time", "start_time")
    following_start = _record_datetime(following, "start_time", "end_time")
    if not current_start or not current_end or not following_start:
        return False
    return (
        following_start >= current_start
        and 0 <= (following_start - current_end).total_seconds() <= PAIR_WINDOW_SECONDS
    )


def _record_datetime(record: dict[str, Any], field: str, fallback_field: str) -> datetime | None:
    value = str(record.get(field) or record.get(fallback_field) or "")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_highlighted(group: dict[str, Any]) -> bool:
    records = group.get("records") or []
    return len(records) > 1 or (records and records[0].get("direction") == "双向")


def _record_values(record: dict[str, Any]) -> list[str]:
    start = _format_minute(record.get("start_time"))
    end = _format_minute(record.get("end_time"))
    time_text = f"{start} - {end}" if start != "-" or end != "-" else "-"
    return [
        str(record.get("start_time") or "")[:10] or "-",
        time_text,
        str(record.get("direction") or "-") or "-",
        str(record.get("responsible_person") or "-") or "-",
        str(record.get("recorder") or "-") or "-",
    ]


def _format_minute(value: Any) -> str:
    try:
        return datetime.fromisoformat(str(value)).strftime("%H:%M")
    except (TypeError, ValueError):
        return "-"


def _wrap_text(value: str, max_width: int, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, *, preserve_name_groups: bool = False) -> list[str]:
    if preserve_name_groups:
        parts = [part for part in re_split_names(value) if part]
        lines: list[str] = []
        line = ""
        for part in parts:
            candidate = f"{line}、{part}" if line else part
            if line and _text_width(candidate, font) > max_width:
                lines.append(line)
                line = part
            else:
                line = candidate
        return lines + ([line] if line else ["-"])
    text = str(value or "-")
    lines: list[str] = []
    line = ""
    for char in text:
        candidate = f"{line}{char}"
        if line and _text_width(candidate, font) > max_width:
            lines.append(line)
            line = char
        else:
            line = candidate
    return lines + ([line] if line else ["-"])


def re_split_names(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[，,、；;/\s]+", str(value or "").strip()) if part.strip()]


def _draw_wrapped_centered(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], value: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, *, preserve_name_groups: bool = False) -> None:
    left, top, right, bottom = box
    lines = _wrap_text(value, max(20, right - left - 24), font, preserve_name_groups=preserve_name_groups)
    line_height = 28
    start_y = (top + bottom) / 2 - (len(lines) - 1) * line_height / 2
    for index, line in enumerate(lines):
        text_box = draw.textbbox((0, 0), line, font=font)
        x = (left + right - text_box[2] + text_box[0]) / 2
        y = start_y + index * line_height - (text_box[3] + text_box[1]) / 2
        draw.text((x, y), line, font=font, fill="#111827")


def _draw_centered(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], value: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, fill: str) -> None:
    _draw_wrapped_centered(draw, box, value, font)
    # Header text is single-line; the helper above keeps its vertical centering consistent.


def _draw_grid(draw: ImageDraw.ImageDraw, x: int, y: int, width: int, height: int, column_widths: list[int], color: str, *, row_heights: list[int] | None = None) -> None:
    current_x = x
    for column_width in column_widths:
        draw.line((current_x, y, current_x, y + height), fill=color, width=1)
        current_x += column_width
    draw.line((current_x, y, current_x, y + height), fill=color, width=1)
    draw.line((x, y, x + width, y), fill=color, width=1)
    draw.line((x, y + height, x + width, y + height), fill=color, width=1)
    if row_heights:
        row_y = y
        for row_height in row_heights[:-1]:
            row_y += row_height
            draw.line((x + column_widths[0], row_y, x + width, row_y), fill=color, width=1)


def _draw_group_border(draw: ImageDraw.ImageDraw, x: int, y: int, width: int, height: int, *, skip_top: bool = False) -> None:
    color = "#111827"
    border = 4
    if not skip_top:
        draw.rectangle((x + border, y, x + width - border, y + border), fill=color)
    draw.rectangle((x + border, y + height - border, x + width - border, y + height), fill=color)
    draw.rectangle((x, y, x + border, y + height), fill=color)
    draw.rectangle((x + width - border, y, x + width, y + height), fill=color)


def _text_width(value: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> float:
    if hasattr(font, "getlength"):
        return float(font.getlength(value))
    left, _, right, _ = font.getbbox(value)
    return float(right - left)
