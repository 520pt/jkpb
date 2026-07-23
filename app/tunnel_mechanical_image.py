from __future__ import annotations

from datetime import date
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app.daily_duty_image import _font


WIDTH = 1680
LEFT = 24
TOP = 22
ROW_HEIGHT = 70


def render_tunnel_mechanical_result_image(
    rows: list[dict[str, Any]],
    *,
    check_time: date,
    checker: str,
    recorder: str,
    title: str = "隧道机电录入查询结果",
) -> bytes:
    fonts = {
        "title": _font(24, bold=True),
        "meta": _font(16),
        "header": _font(15, bold=True),
        "body": _font(15),
        "small": _font(13),
    }
    columns = [
        ("序号", 52, "index"),
        ("路线编码", 78, "routeCode"),
        ("隧道名称", 170, "assetName"),
        ("管养单位", 116, "deptName"),
        ("检查日期", 100, "checkTime"),
        ("天气", 60, "weather"),
        ("负责人", 80, "checker"),
        ("记录人", 80, "recorder"),
        ("设备名称", 150, "devName"),
        ("检查位置", 150, "location"),
        ("检查内容", 150, "content"),
        ("检查结果", 82, "resultText"),
        ("车牌", 92, "carLicense"),
        ("数量", 52, "nums"),
    ]
    table_width = sum(width for _, width, _ in columns)
    width = max(WIDTH, table_width + LEFT * 2)
    visible_rows = rows[:20]
    empty_height = 110 if not visible_rows else 0
    height = TOP + 68 + 42 + max(1, len(visible_rows)) * ROW_HEIGHT + empty_height + 26

    image = Image.new("RGB", (width, height), "#f6f8fb")
    draw = ImageDraw.Draw(image)

    _rounded(draw, (LEFT, TOP, width - LEFT, TOP + 54), 8, "#172033")
    draw.text((LEFT + 18, TOP + 14), title, font=fonts["title"], fill="#ffffff")
    meta = f"日期 {check_time.isoformat()}    负责人 {checker or '-'}    记录人 {recorder or '-'}    共 {len(rows)} 条"
    draw.text((width - LEFT - _text_width(meta, fonts["meta"]) - 18, TOP + 19), meta, font=fonts["meta"], fill="#dbeafe")

    x = LEFT
    y = TOP + 78
    for label, col_width, _ in columns:
        draw.rectangle((x, y, x + col_width, y + 42), fill="#eef2f7", outline="#d9e1ec")
        _center_text(draw, label, x, y + 12, col_width, fonts["header"], "#172033")
        x += col_width

    if not visible_rows:
        y += 42
        draw.rectangle((LEFT, y, LEFT + table_width, y + empty_height), fill="#ffffff", outline="#d9e1ec")
        message = "平台查询成功，但没有查到匹配记录"
        _center_text(draw, message, LEFT, y + 42, table_width, fonts["title"], "#64748b")
    else:
        for row_index, row in enumerate(visible_rows, start=1):
            x = LEFT
            y = TOP + 120 + (row_index - 1) * ROW_HEIGHT
            fill = "#ffffff" if row_index % 2 else "#fbfdff"
            draw.rectangle((LEFT, y, LEFT + table_width, y + ROW_HEIGHT), fill=fill, outline="#d9e1ec")
            for _, col_width, key in columns:
                value = row_index if key == "index" else row.get(key, "")
                lines = _wrap_text(str(value or "-"), col_width - 12, fonts["body"], max_lines=2)
                line_y = y + 12 if len(lines) == 1 else y + 8
                for line in lines:
                    draw.text((x + 6, line_y), line, font=fonts["body"], fill="#172033")
                    line_y += 22
                draw.line((x + col_width, y, x + col_width, y + ROW_HEIGHT), fill="#d9e1ec")
                x += col_width

    if len(rows) > len(visible_rows):
        footer = f"图片只展示前 {len(visible_rows)} 条，完整结果请到平台查看。"
        draw.text((LEFT, height - 22), footer, font=fonts["small"], fill="#64748b")

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _wrap_text(value: str, max_width: int, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, *, max_lines: int) -> list[str]:
    lines: list[str] = []
    line = ""
    for char in str(value or "-"):
        candidate = f"{line}{char}"
        if line and _text_width(candidate, font) > max_width:
            lines.append(line)
            line = char
            if len(lines) == max_lines:
                break
        else:
            line = candidate
    if len(lines) < max_lines and line:
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    if lines and len(lines) == max_lines and _text_width(lines[-1], font) > max_width:
        lines[-1] = _ellipsize(lines[-1], max_width, font)
    return lines or ["-"]


def _ellipsize(value: str, max_width: int, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> str:
    text = value
    while text and _text_width(f"{text}...", font) > max_width:
        text = text[:-1]
    return f"{text}..." if text else "..."


def _center_text(
    draw: ImageDraw.ImageDraw,
    value: str,
    x: int,
    y: int,
    width: int,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: str,
) -> None:
    draw.text((x + max(0, (width - _text_width(value, font)) / 2), y), value, font=font, fill=fill)


def _text_width(value: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> float:
    if hasattr(font, "getlength"):
        return float(font.getlength(value))
    left, _, right, _ = font.getbbox(value)
    return float(right - left)


def _rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int, fill: str, outline: str | None = None) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline)
