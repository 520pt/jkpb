from __future__ import annotations

import os
import re
from datetime import datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app.reminders import ReminderEvent


WIDTH = 900
CARD_MARGIN = 24
INNER_LEFT = 56
INNER_RIGHT = 844
BG = "#f2f6fb"
LINE = "#dbeafe"
INK = "#0f172a"
MUTED = "#64748b"
BLUE = "#2563eb"
ORANGE = "#d97706"
RED = "#dc2626"
GREEN = "#059669"
PURPLE = "#7c3aed"

SHIFT_THEME = {
    "早班": {"main": "#0d8071", "soft": "#ccfbf1", "text": "#0f766e"},
    "中班": {"main": "#2563eb", "soft": "#dbeafe", "text": "#2563eb"},
    "晚班": {"main": "#7c3aed", "soft": "#ede9fe", "text": "#7c3aed"},
    "夜班": {"main": "#7c3aed", "soft": "#ede9fe", "text": "#7c3aed"},
}

REMINDER_CONTENT_RE = re.compile(
    r"^(?P<name>.+?) (?P<date>\d{4}-\d{2}-\d{2})（(?P<time_range>[^)]+)\)是你的(?P<shift_label>早班|中班|晚班|夜班)$"
)


def render_shift_reminder_image(event: ReminderEvent) -> bytes:
    items = [_parse_content_line(line, event.person_name) for line in str(event.content or "").splitlines() if line.strip()]
    items = [item for item in items if item]
    if not items:
        return _render_generic_shift_image(event)

    if len(items) == 1:
        return _render_single_item(items[0])
    return _render_combined_items(event.person_name, items)


def _parse_content_line(line: str, fallback_name: str) -> dict[str, str] | None:
    match = REMINDER_CONTENT_RE.match(line.strip())
    if not match:
        return None
    shift_label = match.group("shift_label")
    if shift_label == "夜班":
        shift_label = "晚班"
    return {
        "person": match.group("name") or fallback_name,
        "date": match.group("date"),
        "weekday": _weekday_label(match.group("date")),
        "shift_label": shift_label,
        "time_range": match.group("time_range").replace("至", " 至 "),
    }


def _render_single_item(item: dict[str, str]) -> bytes:
    dummy = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    content_h = _content_card_height(dummy, INNER_RIGHT - INNER_LEFT, item["shift_label"])
    height = max(585, 48 + 90 + 28 + 82 + 20 + 76 + 28 + content_h + 52)
    image = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(image)
    _rounded(draw, (CARD_MARGIN, 24, WIDTH - CARD_MARGIN, height - 24), 30, "#ffffff", "#cbd5e1", 2)
    theme = _theme(item["shift_label"])
    _draw_header(draw, (48, 48, 852, 138), theme["main"], "7:50 每日提醒")
    y = 166
    y = _draw_date_person_row(draw, INNER_LEFT, y, INNER_RIGHT, item=item)
    y = _draw_shift_time_row(draw, INNER_LEFT, y, INNER_RIGHT, item=item)
    _draw_content_card(draw, INNER_LEFT, y + 8, INNER_RIGHT, item["shift_label"])
    return _png_bytes(image)


def _render_combined_items(person: str, items: list[dict[str, str]]) -> bytes:
    dummy = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    blocks = [
        76 + 16 + _content_card_height(dummy, INNER_RIGHT - INNER_LEFT, item["shift_label"])
        for item in items
    ]
    height = 48 + 90 + 28 + 74 + 22 + sum(blocks) + max(0, len(items) - 1) * 38 + 48
    image = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(image)
    _rounded(draw, (CARD_MARGIN, 24, WIDTH - CARD_MARGIN, height - 24), 30, "#ffffff", "#cbd5e1", 2)
    _draw_header(draw, (48, 48, 852, 138), "#1e40af", "7:50 每日提醒 · 同一人多班次合并")
    y = 166
    y = _draw_person_only_row(draw, INNER_LEFT, y, INNER_RIGHT, person or items[0]["person"])
    for index, item in enumerate(items):
        y = _draw_compact_shift_row(draw, INNER_LEFT, y, INNER_RIGHT, item=item)
        y = _draw_content_card(draw, INNER_LEFT, y, INNER_RIGHT, item["shift_label"])
        if index != len(items) - 1:
            y += 18
            draw.line((72, y, 828, y), fill="#e2e8f0", width=2)
            y += 18
    return _png_bytes(image)


def _render_generic_shift_image(event: ReminderEvent) -> bytes:
    text = str(event.content or "").strip() or "监控班提醒"
    font_body = _font(25)
    dummy = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    lines = _wrap_plain(dummy, text, font_body, INNER_RIGHT - INNER_LEFT - 48)
    content_h = 82 + len(lines) * 34
    height = max(430, 48 + 90 + 28 + 74 + 24 + content_h + 52)
    image = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(image)
    _rounded(draw, (CARD_MARGIN, 24, WIDTH - CARD_MARGIN, height - 24), 30, "#ffffff", "#cbd5e1", 2)
    _draw_header(draw, (48, 48, 852, 138), "#2563eb", _header_subtitle(event), _header_title(event))
    y = _draw_person_only_row(draw, INNER_LEFT, 166, INNER_RIGHT, event.person_name)
    box = (INNER_LEFT, y + 8, INNER_RIGHT, height - 52)
    _rounded(draw, box, 20, "#f8fafc", LINE, 1)
    draw.text((box[0] + 24, box[1] + 22), "提醒内容", font=_font(20), fill=BLUE)
    _draw_plain_lines(draw, lines, box[0] + 24, box[1] + 62, font_body, INK, 34)
    return _png_bytes(image)


def _header_title(event: ReminderEvent) -> str:
    kind = str(event.kind or "")
    if kind.startswith("custom"):
        return "自定义提醒"
    if kind.startswith("vacation"):
        return "假期余额提醒"
    if kind.startswith("rest"):
        return "休息提醒"
    return "监控班提醒"


def _header_subtitle(event: ReminderEvent) -> str:
    try:
        return f"{event.send_at:%H:%M} 提醒"
    except Exception:
        return "提醒"


def _draw_header(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], main: str, subtitle: str, title: str = "监控班提醒") -> None:
    _rounded(draw, box, 22, main)
    title_font = _font(34)
    sub_font = _font(20)
    title_h = _text_size(draw, title, title_font)[1]
    sub_h = _text_size(draw, subtitle, sub_font)[1]
    gap = 10
    total = title_h + gap + sub_h
    top = box[1] + (box[3] - box[1] - total) / 2
    _draw_vcenter_text(draw, box[0] + 28, top + title_h / 2, title, title_font, "#ffffff")
    _draw_vcenter_text(draw, box[0] + 28, top + title_h + gap + sub_h / 2, subtitle, sub_font, "#eff6ff")


def _draw_date_person_row(draw: ImageDraw.ImageDraw, x: int, y: int, right: int, *, item: dict[str, str]) -> int:
    theme = _theme(item["shift_label"])
    date_box = (x, y, x + 274, y + 82)
    _rounded(draw, date_box, 18, "#f8fafc", LINE, 1)
    _draw_vcenter_text(draw, date_box[0] + 22, date_box[1] + 48, item["date"], _font(27), INK)
    _draw_week_badge(draw, date_box, item["weekday"], theme)
    person_box = (x + 294, y, right, y + 82)
    _rounded(draw, person_box, 18, "#f8fafc", LINE, 1)
    _draw_vcenter_text(draw, person_box[0] + 24, person_box[1] + 41, "人员", _font(17), MUTED)
    _draw_vcenter_text(draw, person_box[0] + 96, person_box[1] + 41, item["person"], _font(31), INK)
    return y + 102


def _draw_person_only_row(draw: ImageDraw.ImageDraw, x: int, y: int, right: int, person: str) -> int:
    box = (x, y, right, y + 74)
    _rounded(draw, box, 18, "#f8fafc", LINE, 1)
    _draw_vcenter_text(draw, x + 24, y + 37, "人员", _font(17), MUTED)
    _draw_vcenter_text(draw, x + 96, y + 37, person or "-", _font(31), INK)
    return y + 94


def _draw_shift_time_row(draw: ImageDraw.ImageDraw, x: int, y: int, right: int, *, item: dict[str, str]) -> int:
    theme = _theme(item["shift_label"])
    shift_box = (x, y, x + 184, y + 76)
    _rounded(draw, shift_box, 18, theme["soft"], theme["main"], 2)
    _draw_centered_text(draw, shift_box, item["shift_label"], _font(31), theme["text"])
    time_box = (x + 206, y, right, y + 76)
    _rounded(draw, time_box, 18, "#f8fafc", LINE, 1)
    _draw_vcenter_text(draw, time_box[0] + 28, time_box[1] + 38, item["time_range"], _font(30), INK)
    return y + 96


def _draw_compact_shift_row(draw: ImageDraw.ImageDraw, x: int, y: int, right: int, *, item: dict[str, str]) -> int:
    theme = _theme(item["shift_label"])
    date_box = (x, y, x + 274, y + 76)
    _rounded(draw, date_box, 18, "#f8fafc", LINE, 1)
    _draw_vcenter_text(draw, date_box[0] + 22, date_box[1] + 45, item["date"], _font(27), INK)
    _draw_week_badge(draw, date_box, item["weekday"], theme)
    shift_box = (x + 294, y, x + 478, y + 76)
    _rounded(draw, shift_box, 18, theme["soft"], theme["main"], 2)
    _draw_centered_text(draw, shift_box, item["shift_label"], _font(31), theme["text"])
    time_box = (x + 500, y, right, y + 76)
    _rounded(draw, time_box, 18, "#f8fafc", LINE, 1)
    _draw_vcenter_text(draw, time_box[0] + 28, time_box[1] + 38, item["time_range"], _font(30), INK)
    return y + 92


def _draw_content_card(draw: ImageDraw.ImageDraw, x: int, y: int, right: int, shift_label: str) -> int:
    height = _content_card_height(draw, right - x, shift_label)
    box = (x, y, right, y + height)
    _rounded(draw, box, 20, "#f8fafc", LINE, 1)
    draw.text((x + 24, y + 22), "提醒内容", font=_font(20), fill=BLUE)
    lines = _layout_tokens(draw, _content_tokens(shift_label), right - x - 48, _font(24))
    _draw_token_lines(draw, lines, x + 24, y + 60, _font(24), 12)
    return y + height


def _content_card_height(draw: ImageDraw.ImageDraw, width: int, shift_label: str) -> int:
    lines = _layout_tokens(draw, _content_tokens(shift_label), width - 48, _font(24))
    return 22 + 38 + _token_lines_height(draw, lines, _font(24), 12) + 24


def _content_tokens(shift_label: str) -> list[dict[str, Any]]:
    if shift_label == "早班":
        return [
            _token("请注意今晚凌晨"),
            _token("00:00至08:00", ORANGE, "#fff7ed"),
            _token("是你的"),
            _token("早班", GREEN, "#dcfce7"),
            _token("，记得检查"),
            _token("隧道灯", ORANGE, "#fff7ed"),
            _token("是否关闭", RED, "#fee2e2"),
            _token("，"),
            _token("7点50分", ORANGE, "#fff7ed"),
            _token("记得"),
            _token("开启", GREEN, "#dcfce7"),
            _token("隧道灯", ORANGE, "#fff7ed"),
            _token("。"),
        ]
    if shift_label == "中班":
        return [
            _token("请注意今天"),
            _token("08:00至16:00", ORANGE, "#fff7ed"),
            _token("是你的"),
            _token("中班", BLUE, "#dbeafe"),
            _token("，记得写一二楼的"),
            _token("卫生间", ORANGE, "#fff7ed"),
            _token("消毒清洁记录", RED, "#fee2e2"),
            _token("。"),
        ]
    return [
        _token("请注意今天下午"),
        _token("16:00至00:00", ORANGE, "#fff7ed"),
        _token("是你的"),
        _token("晚班", PURPLE, "#ede9fe"),
        _token("，记得在晚上"),
        _token("21点", ORANGE, "#fff7ed"),
        _token("关闭", RED, "#fee2e2"),
        _token("隧道灯", ORANGE, "#fff7ed"),
        _token("。"),
    ]


def _token(text: str, fill: str = INK, bg: str | None = None) -> dict[str, Any]:
    return {"text": text, "fill": fill, "bg": bg, "font": _font(24), "padx": 5 if bg else 0, "pady": 4 if bg else 0}


def _layout_tokens(draw: ImageDraw.ImageDraw, tokens: list[dict[str, Any]], max_width: int, base_font: ImageFont.ImageFont) -> list[list[dict[str, Any]]]:
    lines: list[list[dict[str, Any]]] = []
    line: list[dict[str, Any]] = []
    line_width = 0
    for token in tokens:
        width = _token_width(draw, token, base_font)
        if line and line_width + width > max_width:
            lines.append(line)
            line = [token]
            line_width = width
        else:
            line.append(token)
            line_width += width
    if line:
        lines.append(line)
    return lines


def _draw_token_lines(draw: ImageDraw.ImageDraw, lines: list[list[dict[str, Any]]], x: int, y: int, base_font: ImageFont.ImageFont, gap: int) -> None:
    for line in lines:
        height = _token_line_height(draw, line, base_font)
        cursor = x
        for token in line:
            font = token.get("font", base_font)
            text = token["text"]
            pad_x = int(token.get("padx") or 0)
            text_w, text_h, text_box = _text_size(draw, text, font)
            if token.get("bg"):
                _rounded(draw, (cursor, y, cursor + text_w + pad_x * 2, y + height), 8, token["bg"])
            draw.text((cursor + pad_x - text_box[0], y + (height - text_h) / 2 - text_box[1]), text, font=font, fill=token.get("fill", INK))
            cursor += text_w + pad_x * 2
        y += height + gap


def _token_lines_height(draw: ImageDraw.ImageDraw, lines: list[list[dict[str, Any]]], base_font: ImageFont.ImageFont, gap: int) -> int:
    if not lines:
        return 0
    return sum(_token_line_height(draw, line, base_font) for line in lines) + (len(lines) - 1) * gap


def _token_line_height(draw: ImageDraw.ImageDraw, line: list[dict[str, Any]], base_font: ImageFont.ImageFont) -> int:
    height = 0
    for token in line:
        font = token.get("font", base_font)
        text_h = _text_size(draw, token["text"], font)[1]
        height = max(height, text_h + int(token.get("pady") or 0) * 2)
    return height


def _token_width(draw: ImageDraw.ImageDraw, token: dict[str, Any], base_font: ImageFont.ImageFont) -> int:
    font = token.get("font", base_font)
    return _text_size(draw, token["text"], font)[0] + int(token.get("padx") or 0) * 2


def _draw_week_badge(draw: ImageDraw.ImageDraw, date_box: tuple[int, int, int, int], weekday: str, theme: dict[str, str]) -> None:
    week_w = _text_size(draw, weekday, _font(18))[0]
    box = (date_box[2] - week_w - 36, date_box[1] + 9, date_box[2] - 16, date_box[1] + 37)
    _rounded(draw, box, 10, theme["soft"])
    _draw_centered_text(draw, box, weekday, _font(18), theme["text"])


def _theme(shift_label: str) -> dict[str, str]:
    return SHIFT_THEME.get(shift_label, SHIFT_THEME["中班"])


def _weekday_label(date_text: str) -> str:
    try:
        weekday = datetime.fromisoformat(date_text).weekday()
    except ValueError:
        return ""
    return ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][weekday]


def _wrap_plain(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = f"{current}{char}"
        if current and _text_size(draw, candidate, font)[0] > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _draw_plain_lines(draw: ImageDraw.ImageDraw, lines: list[str], x: int, y: int, font: ImageFont.ImageFont, fill: str, line_height: int) -> None:
    for index, line in enumerate(lines):
        draw.text((x, y + index * line_height), line, font=font, fill=fill)


def _draw_vcenter_text(draw: ImageDraw.ImageDraw, x: float, center_y: float, text: str, font: ImageFont.ImageFont, fill: str) -> None:
    _, text_h, text_box = _text_size(draw, text, font)
    draw.text((x - text_box[0], center_y - text_h / 2 - text_box[1]), text, font=font, fill=fill)


def _draw_centered_text(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, font: ImageFont.ImageFont, fill: str) -> None:
    text_w, text_h, text_box = _text_size(draw, text, font)
    draw.text((box[0] + (box[2] - box[0] - text_w) / 2 - text_box[0], box[1] + (box[3] - box[1] - text_h) / 2 - text_box[1]), text, font=font, fill=fill)


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int, tuple[int, int, int, int]]:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1], box


def _rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int, fill: str, outline: str | None = None, width: int = 1) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


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
