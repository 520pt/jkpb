from __future__ import annotations

import os
from datetime import datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


WIDTH = 1200
LEFT = 28
GAP = 14
HEADER_HEIGHT = 78
BLOCK_BOTTOM_PADDING = 18


def render_daily_duty_image(preview: dict[str, Any]) -> bytes:
    details = preview.get("details") or {}
    image = Image.new("RGB", (WIDTH, 675), "#eef5fb")
    draw = ImageDraw.Draw(image)
    fonts = {
        "title": _font(34, bold=True),
        "section": _font(22, bold=True),
        "label": _font(16, bold=True),
        "label_small": _font(15, bold=True),
        "body": _font(20, bold=True),
        "body_small": _font(18, bold=True),
        "date": _font(17),
        "muted": _font(17),
    }

    date_text, weekday_text = _date_label(str(preview.get("send_at") or ""))
    title_box = (30, 22, WIDTH - 30, 92)
    _rounded(draw, title_box, 26, "#ffffff", "#d8e5ef")
    _draw_centered_y_text(draw, title_box, 56, "今日在岗", fonts["title"], "#224565")
    meta_text = "　".join(part for part in (date_text, weekday_text) if part)
    if meta_text:
        meta_x = WIDTH - 56 - int(_text_width(meta_text, fonts["date"]))
        _draw_centered_y_text(draw, title_box, meta_x, meta_text, fonts["date"], "#516d89")

    left_x, top_y = 30, 116
    left_w, right_x, right_w = 575, 622, 548
    top_card_h, rest_y, bottom_y = 232, 369, 644
    half_w = (left_w - 14) // 2

    monitor_box = (left_x, top_y, left_x + half_w, top_y + top_card_h)
    patrol_box = (left_x + half_w + 14, top_y, left_x + left_w, top_y + top_card_h)
    rest_box = (left_x, rest_y, left_x + left_w, bottom_y)
    right_box = (right_x, top_y, right_x + right_w, bottom_y)

    monitor_color = "#0f766e"
    patrol_color = "#15803d"
    rest_color = "#ea580c"
    right_color = "#e11d48"

    _draw_monitor_panel(draw, monitor_box, monitor_color, fonts, details)
    _draw_patrol_panel(draw, patrol_box, patrol_color, fonts, details, date_text)
    _draw_rest_panel(draw, rest_box, rest_color, fonts, details)
    _draw_status_panel(draw, right_box, right_color, fonts, details)

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _date_label(raw: str) -> tuple[str, str]:
    value = raw.strip()
    if not value:
        return "", ""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value[:10], ""
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    return parsed.strftime("%Y-%m-%d"), weekdays[parsed.weekday()]


def _detail(details: dict[str, Any], key: str, default: str = "无") -> str:
    value = str(details.get(key) or "").strip()
    return value if value else default


def _join_values(values: list[str]) -> str:
    seen: list[str] = []
    for value in values:
        for name in _split_names(value):
            if name and name != "无" and name not in seen:
                seen.append(name)
    return "，".join(seen) if seen else "无"


def _split_names(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text or text == "无":
        return []
    for sep in ("、", ",", "，", "|", "/", " "):
        text = text.replace(sep, "，")
    return [part.strip() for part in text.split("，") if part.strip() and part.strip() != "无"]


def _draw_card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], *, outline: str = "#d8e5ef") -> None:
    x1, y1, x2, y2 = box
    _rounded(draw, (x1 + 2, y1 + 3, x2 + 2, y2 + 3), 22, "#dfe9f2")
    _rounded(draw, box, 22, "#ffffff", outline)


def _draw_panel_header(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    title: str,
    color: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    *,
    right_text: str = "",
) -> None:
    x1, y1, x2, _ = box
    header = (x1, y1, x2, y1 + 55)
    _rounded(draw, header, 22, color)
    draw.rectangle((x1, y1 + 36, x2, y1 + 55), fill=color)
    _draw_centered_y_text(draw, header, x1 + 20, title, font, "#ffffff")
    if right_text:
        right_font = _font(16, bold=True)
        right_x = x2 - 20 - int(_text_width(right_text, right_font))
        _draw_centered_y_text(draw, header, right_x, right_text, right_font, "#eaf6ff")


def _draw_labeled_value(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    label: str,
    value: str,
    accent: str,
    fonts: dict[str, ImageFont.FreeTypeFont | ImageFont.ImageFont],
    *,
    fill: str = "#f8fbff",
    outline: str = "#d8e5ef",
) -> None:
    x1, y1, x2, y2 = box
    _rounded(draw, box, 12, fill, outline)
    compact = y2 - y1 <= 62
    value_font = _font(16, bold=True) if compact and value != "无" else (fonts["body_small"] if value != "无" else fonts["muted"])
    label_y = y1 + (9 if compact else 13)
    value_y = y1 + (32 if compact else 45)
    draw.text((x1 + 13, label_y), label, font=fonts["label_small"], fill=accent)
    lines = _wrap_text(value, x2 - x1 - 26, value_font)
    line_font = value_font
    line_fill = "#172033" if value != "无" else "#7794a8"
    _draw_lines(draw, lines[:2 if compact else 3], x1 + 13, value_y, line_font, line_fill, 21 if compact else 24)


def _draw_monitor_panel(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    accent: str,
    fonts: dict[str, ImageFont.FreeTypeFont | ImageFont.ImageFont],
    details: dict[str, Any],
) -> None:
    _draw_card(draw, box, outline="#cfe6df")
    _draw_panel_header(draw, box, "监控", accent, fonts["section"])
    x1, y1, x2, _ = box
    cell_w = (x2 - x1 - 48) // 2
    cell_h = 58
    cells = [
        ("今日早班", _detail(details, "early")),
        ("明日早班", _detail(details, "tomorrow_early")),
        ("中班", _detail(details, "middle")),
        ("晚班", _detail(details, "night")),
    ]
    for index, (label, value) in enumerate(cells):
        row, col = divmod(index, 2)
        cx = x1 + 18 + col * (cell_w + 10)
        cy = y1 + 74 + row * (cell_h + 14)
        _draw_labeled_value(draw, (cx, cy, cx + cell_w, cy + cell_h), label, value, accent, fonts, fill="#f7fbff", outline="#cfe5ee")


def _draw_patrol_panel(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    accent: str,
    fonts: dict[str, ImageFont.FreeTypeFont | ImageFont.ImageFont],
    details: dict[str, Any],
    date_text: str,
) -> None:
    patrol_people = _detail(details, "patrol", _detail(details, "standby"))
    patrol_date = _detail(details, "patrol_date", _compact_date(date_text) or "今日")
    _draw_card(draw, box, outline="#cfe6df")
    _draw_panel_header(draw, box, "巡查", accent, fonts["section"], right_text=f"巡查日期 {patrol_date}")
    x1, y1, x2, _ = box
    content_x = x1 + 20
    content_w = x2 - x1 - 40
    y = y1 + 72
    draw.text((content_x, y), "巡查人员", font=fonts["label"], fill=accent)
    _draw_lines(draw, _wrap_text(patrol_people, content_w, fonts["body_small"]), content_x, y + 29, fonts["body_small"] if patrol_people != "无" else fonts["muted"], "#172033" if patrol_people != "无" else "#7794a8", 24)


def _draw_rest_panel(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    accent: str,
    fonts: dict[str, ImageFont.FreeTypeFont | ImageFont.ImageFont],
    details: dict[str, Any],
) -> None:
    _draw_card(draw, box, outline="#d8e5ef")
    x1, y1, x2, y2 = box
    header = (x1 + 20, y1 + 14, x2 - 20, y1 + 58)
    _draw_centered_y_text(draw, header, x1 + 20, "休息状态", fonts["section"], accent)
    draw.line((x1 + 20, y1 + 58, x2 - 20, y1 + 58), fill="#d8e5ef", width=2)
    items = [
        ("今日下午休息", _detail(details, "afternoon_rest")),
        ("正在休息", _detail(details, "resting")),
        ("今日下午到岗", _detail(details, "afternoon_return")),
    ]
    inner_x, inner_y = x1 + 20, y1 + 78
    card_w = (x2 - x1 - 54) // 3
    card_h = y2 - inner_y - 20
    for index, (label, value) in enumerate(items):
        cx = inner_x + index * (card_w + 14)
        card = (cx, inner_y, cx + card_w, inner_y + card_h)
        _rounded(draw, card, 12, "#fff9f2", "#fed7aa")
        draw.text((cx + 13, inner_y + 14), label, font=fonts["label"], fill=accent)
        draw.line((cx + 13, inner_y + 43, cx + card_w - 13, inner_y + 43), fill="#fdba74", width=2)
        _draw_name_grid(draw, value, (cx + 13, inner_y + 56, cx + card_w - 13, inner_y + card_h - 12), accent="#172033", muted="#c17735")


def _draw_status_panel(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    accent: str,
    fonts: dict[str, ImageFont.FreeTypeFont | ImageFont.ImageFont],
    details: dict[str, Any],
) -> None:
    _draw_card(draw, box, outline="#d8e5ef")
    x1, y1, x2, y2 = box
    draw.text((x1 + 18, y1 + 18), "在岗状态", font=fonts["section"], fill=accent)
    subtitle = "按岗位汇总"
    subtitle_x = x2 - 18 - int(_text_width(subtitle, fonts["date"]))
    draw.text((subtitle_x, y1 + 24), subtitle, font=fonts["date"], fill="#64748b")
    draw.line((x1 + 18, y1 + 58, x2 - 18, y1 + 58), fill="#d8e5ef", width=2)

    monitor_people = _join_values([_detail(details, "early"), _detail(details, "middle"), _detail(details, "night"), _detail(details, "tomorrow_early")])
    patrol_people = _detail(details, "patrol", _detail(details, "standby"))
    items = [
        ("巡查班", patrol_people),
        ("监控班", monitor_people),
        ("站管", _detail(details, "station", _detail(details, "station_managers"))),
        ("办公室", _detail(details, "office")),
        ("小车驾驶员", _detail(details, "small_drivers")),
        ("大车驾驶员", _detail(details, "big_drivers")),
    ]
    row_top = y1 + 70
    row_h = max(58, (y2 - row_top - 20) // len(items))
    for index, (label, value) in enumerate(items):
        ry = row_top + index * row_h
        draw.line((x1 + 18, ry + row_h, x2 - 18, ry + row_h), fill="#e5edf5", width=1)
        pill_w = 86 if len(label) <= 4 else 112
        pill_h = 34
        pill_y = ry + (row_h - pill_h) // 2
        pill = (x1 + 24, pill_y, x1 + 24 + pill_w, pill_y + pill_h)
        _rounded(draw, pill, 13, "#ffffff", "#d8e5ef")
        label_x = pill[0] + max(8, int((pill_w - _text_width(label, fonts["label_small"])) / 2))
        _draw_centered_y_text(draw, pill, label_x, label, fonts["label_small"], accent)
        _draw_status_value(draw, value, (pill[2] + 22, ry + 6, x2 - 26, ry + row_h - 6), accent="#172033", muted="#7d8ca3")


def _draw_status_value(
    draw: ImageDraw.ImageDraw,
    value: str,
    box: tuple[int, int, int, int],
    *,
    accent: str,
    muted: str,
) -> None:
    x1, y1, x2, y2 = box
    text = str(value or "").strip() or "无"
    fill = accent if text != "无" else muted
    best_font = _font(18, bold=True) if text != "无" else _font(17)
    best_lines = _wrap_text(text, x2 - x1, best_font)
    best_line_h = 25
    for size in (18, 17, 16, 15, 14, 13, 12):
        font = _font(size, bold=True) if text != "无" else _font(size)
        line_h = size + 7
        lines = _wrap_text(text, x2 - x1, font)
        if len(lines) * line_h <= y2 - y1:
            best_font = font
            best_lines = lines
            best_line_h = line_h
            break
    start_y = y1 + max(0, ((y2 - y1) - len(best_lines) * best_line_h) // 2)
    _draw_lines(draw, best_lines, x1, start_y, best_font, fill, best_line_h)


def _compact_date(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = datetime.strptime(value[:10], "%Y-%m-%d")
        except ValueError:
            return value[:10]
    return f"{parsed.month}.{parsed.day}"


def _draw_name_grid(
    draw: ImageDraw.ImageDraw,
    value: str,
    box: tuple[int, int, int, int],
    *,
    accent: str,
    muted: str,
    prefer_columns: int | None = None,
) -> None:
    x1, y1, x2, y2 = box
    names = _split_names(value)
    if not names:
        _draw_centered_y_text(draw, box, x1, "无", _font(17), muted)
        return
    columns = prefer_columns or (2 if len(names) <= 8 else 3)
    columns = max(1, min(columns, 3, len(names)))
    best_font = _font(14, bold=True)
    best_line_h = 21
    for size in (18, 17, 16, 15, 14, 13, 12):
        font = _font(size, bold=True)
        line_h = size + 7
        rows = (len(names) + columns - 1) // columns
        col_w = max(1, (x2 - x1 - (columns - 1) * 8) // columns)
        if rows * line_h <= y2 - y1 and all(_text_width(name, font) <= col_w for name in names):
            best_font = font
            best_line_h = line_h
            break
    rows = (len(names) + columns - 1) // columns
    col_w = max(1, (x2 - x1 - (columns - 1) * 8) // columns)
    start_y = y1 + max(0, ((y2 - y1) - rows * best_line_h) // 2)
    for index, name in enumerate(names):
        col = index // rows
        row = index % rows
        draw.text((x1 + col * (col_w + 8), start_y + row * best_line_h), name, font=best_font, fill=accent)

def _wrap_text(value: str, max_width: int, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> list[str]:
    parts = [part for part in value.split("，") if part]
    lines: list[str] = []
    line = ""
    for part in parts:
        for segment in _split_oversized_text(part, max_width, font):
            candidate = f"{line}，{segment}" if line else segment
            if _text_width(candidate, font) > max_width and line:
                lines.append(line)
                line = segment
            else:
                line = candidate
    if line:
        lines.append(line)
    return lines or ["无"]


def _split_oversized_text(value: str, max_width: int, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> list[str]:
    if _text_width(value, font) <= max_width:
        return [value]
    parts: list[str] = []
    line = ""
    for char in value:
        candidate = f"{line}{char}"
        if line and _text_width(candidate, font) > max_width:
            parts.append(line)
            line = char
        else:
            line = candidate
    if line:
        parts.append(line)
    return parts or [value]


def _text_width(value: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> float:
    if hasattr(font, "getlength"):
        return float(font.getlength(value))
    left, _, right, _ = font.getbbox(value)
    return float(right - left)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _candidate_font_paths(bold=bold):
        if path.exists():
            for index in _font_face_indices(path):
                try:
                    return ImageFont.truetype(str(path), size, index=index)
                except OSError:
                    continue
    return ImageFont.load_default()


def has_cjk_font() -> bool:
    return any(path.exists() for path in _candidate_font_paths(bold=False))


def _font_face_indices(path: Path) -> tuple[int, ...]:
    if path.suffix.lower() != ".ttc":
        return (0,)
    if path.name.startswith("NotoSansCJK"):
        return (2, 7, 0, 1, 3, 4, 5, 6, 8, 9)
    return (0,)


@lru_cache(maxsize=4)
def _candidate_font_paths(*, bold: bool) -> list[Path]:
    if bold:
        names = [
            "NotoSansCJKsc-Bold.otf",
            "NotoSansSC-Bold.otf",
            "NotoSansCJK-Bold.ttc",
            "NotoSansCJK-Bold.otf",
            "simhei.ttf",
            "SimHei.ttf",
            "msyhsb.ttc",
            "msyh.ttc",
            "wqy-microhei.ttc",
            "WenQuanYi Micro Hei.ttf",
            "msyhbd.ttc",
        ]
    else:
        names = [
            "NotoSansCJKsc-Regular.otf",
            "NotoSansSC-Regular.otf",
            "NotoSansCJK-Regular.ttc",
            "NotoSansCJK-Regular.otf",
            "msyh.ttc",
            "simhei.ttf",
            "SimHei.ttf",
            "wqy-microhei.ttc",
            "WenQuanYi Micro Hei.ttf",
        ]
    font_dirs = [
        Path("fonts"),
        Path("app/static/fonts"),
        Path("/app/fonts"),
        Path("/app/app/static/fonts"),
        Path("C:/Windows/Fonts"),
        Path("/usr/share/fonts/opentype/noto"),
        Path("/usr/share/fonts/truetype/noto"),
        Path("/usr/share/fonts/truetype/wqy"),
        Path("/usr/share/fonts/truetype/arphic"),
    ]
    paths: list[Path] = []
    env_font = os.getenv("CJK_FONT_PATH", "").strip()
    if env_font:
        paths.append(Path(env_font))
    for font_dir in font_dirs:
        for name in names:
            paths.append(font_dir / name)
        if font_dir.exists():
            for pattern in ("*CJK*.ttc", "*CJK*.otf", "*SansSC*.otf", "*Noto*SC*.otf", "*wqy*.ttc"):
                paths.extend(font_dir.rglob(pattern))
    return paths


def _rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int, fill: str, outline: str | None = None) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline)


def _draw_centered_y_text(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    x: int,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: str,
) -> None:
    text_box = draw.textbbox((0, 0), text, font=font)
    text_height = text_box[3] - text_box[1]
    box_height = box[3] - box[1]
    y = int(box[1] + (box_height - text_height) / 2 - text_box[1])
    draw.text((x, y), text, font=font, fill=fill)


def _draw_lines(draw: ImageDraw.ImageDraw, lines: list[str], x: int, y: int, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, fill: str, line_height: int) -> None:
    for index, line in enumerate(lines):
        draw.text((x, y + index * line_height), line, font=font, fill=fill)
