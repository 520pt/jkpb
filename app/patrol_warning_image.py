from __future__ import annotations

from datetime import datetime, timedelta
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from app.daily_duty_image import _font
from app.patrol_warning import PatrolWarning


WIDTH = 900
LEFT = 28


def render_patrol_warning_image(
    warning: PatrolWarning,
    *,
    now: datetime,
    window_hours: int = 48,
    title: str = "公路巡查预警提醒",
    mode: str = "auto",
) -> bytes:
    image_mode = _image_mode(warning, now, mode)
    title = title if title != "公路巡查预警提醒" else _image_title(warning, image_mode)
    accent = _level_color(warning.warning_level)
    fonts = {
        "title": _font(24, bold=True),
        "level": _font(28, bold=True),
        "label": _font(16, bold=True),
        "body": _font(20, bold=True),
        "small": _font(16),
        "metric": _font(22, bold=True),
    }
    route_text = _route_text(warning)
    stake_text = _stake_range(warning)
    start_text = _format_datetime(warning.start_time)
    end_text = _format_datetime(warning.end_time)
    elapsed_hours = _hours_between(warning.end_time, now)
    remaining_hours = _remaining_hours(warning.end_time, now, window_hours)

    image = Image.new("RGB", (WIDTH, 560), "#f3f6fb")
    draw = ImageDraw.Draw(image)

    _rounded(draw, (LEFT, 22, WIDTH - LEFT, 86), 8, "#172033")
    _draw_text_in_box(draw, (LEFT, 22, WIDTH - LEFT, 86), title, fonts["title"], "#ffffff", padding_x=20)
    _draw_text_in_box(
        draw,
        (WIDTH - LEFT - 240, 22, WIDTH - LEFT - 20, 86),
        now.strftime("%Y-%m-%d %H:%M"),
        fonts["small"],
        "#dbeafe",
        align="right",
    )

    _rounded(draw, (LEFT, 106, WIDTH - LEFT, 206), 8, "#ffffff", "#d7deea")
    _rounded(draw, (LEFT + 18, 126, LEFT + 210, 186), 8, accent)
    _draw_text_in_box(
        draw,
        (LEFT + 18, 126, LEFT + 210, 186),
        warning.warning_level_label or "预警",
        fonts["level"],
        "#ffffff",
        align="center",
    )
    draw.text((LEFT + 236, 127), route_text, font=fonts["body"], fill="#18212f")
    draw.text((LEFT + 236, 162), warning.warn_type_name or "公路巡查APP", font=fonts["small"], fill="#64748b")

    cards = [
        ("预警开始时间", start_text, "#2563eb"),
        ("预警结束时间", end_text, "#0f8a5f"),
        ("桩号范围", stake_text, "#7c3aed"),
    ]
    card_width = (WIDTH - LEFT * 2 - 28) // 3
    for index, (label, value, color) in enumerate(cards):
        x = LEFT + index * (card_width + 14)
        y = 226
        _rounded(draw, (x, y, x + card_width, y + 112), 8, "#ffffff", "#d7deea")
        draw.text((x + 18, y + 18), label, font=fonts["label"], fill=color)
        for line_index, line in enumerate(_wrap_text(value, card_width - 36, fonts["body"])):
            draw.text((x + 18, y + 52 + line_index * 26), line, font=fonts["body"], fill="#18212f")

    _rounded(draw, (LEFT, 360, WIDTH - LEFT, 504), 8, "#ffffff", "#d7deea")
    band_color = "#c2410c" if image_mode == "end" else "#1d4ed8"
    _rounded(draw, (LEFT, 360, WIDTH - LEFT, 406), 8, band_color)
    draw.rectangle((LEFT, 396, WIDTH - LEFT, 406), fill=band_color)
    _draw_text_in_box(
        draw,
        (LEFT, 360, WIDTH - LEFT, 406),
        _patrol_summary_text(warning, window_hours, image_mode),
        fonts["label"],
        "#ffffff",
        padding_x=18,
    )

    metrics = [
        ("已结束", f"{elapsed_hours} 小时"),
        ("倒计时", f"{remaining_hours} 小时"),
        ("状态", _status_text(warning.end_time, now, window_hours)),
    ]
    metric_width = (WIDTH - LEFT * 2 - 64) // 3
    for index, (label, value) in enumerate(metrics):
        x = LEFT + 22 + index * (metric_width + 20)
        y = 424
        _rounded(draw, (x, y, x + metric_width, y + 58), 8, "#fffaf7", "#f2d8ca")
        _draw_text_in_box(draw, (x + 14, y, x + 86, y + 58), label, fonts["small"], "#9a3412")
        _draw_text_in_box(draw, (x + 90, y, x + metric_width - 14, y + 58), value, fonts["metric"], "#18212f", align="center")

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _image_mode(warning: PatrolWarning, now: datetime, mode: str) -> str:
    normalized = str(mode or "auto").strip().lower()
    if normalized in {"start", "end"}:
        return normalized
    return "end" if warning.end_time and now >= warning.end_time else "start"


def _image_title(warning: PatrolWarning, mode: str) -> str:
    if mode == "end":
        return f"最新{warning.warning_level_label or '预警'}已结束"
    return "公路巡查预警提醒"


def _patrol_summary_text(warning: PatrolWarning, window_hours: int, mode: str) -> str:
    if mode == "end":
        frequency = _patrol_frequency_clause(warning)
        return f"预警结束后 {window_hours} 小时内{frequency}"
    return f"预警发布后请关注路段巡查"


def _patrol_frequency_clause(warning: PatrolWarning) -> str:
    text = str(getattr(warning, "patrol_frequency_text", "") or "").strip()
    if not text:
        return "按预警要求巡查"
    return text if text.endswith("巡查") else f"{text}都巡查"


def _level_color(level: str) -> str:
    return {
        "1": "#2563eb",
        "2": "#ca8a04",
        "3": "#ea580c",
        "4": "#dc2626",
    }.get(str(level or ""), "#475569")


def _rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int, fill: str, outline: str | None = None) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline)


def _draw_text_in_box(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: str,
    *,
    padding_x: int = 0,
    align: str = "left",
) -> None:
    left, top, right, bottom = box
    text_box = draw.textbbox((0, 0), str(text), font=font)
    text_left, text_top, text_right, text_bottom = text_box
    if align == "center":
        x = (left + right - text_left - text_right) / 2
    elif align == "right":
        x = right - padding_x - text_right
    else:
        x = left + padding_x - text_left
    y = (top + bottom - text_top - text_bottom) / 2
    draw.text((x, y), str(text), font=font, fill=fill)


def _wrap_text(value: str, max_width: int, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> list[str]:
    lines: list[str] = []
    line = ""
    for char in str(value or "-"):
        candidate = f"{line}{char}"
        if line and _text_width(candidate, font) > max_width:
            lines.append(line)
            line = char
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines or ["-"]


def _text_width(value: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> float:
    if hasattr(font, "getlength"):
        return float(font.getlength(value))
    left, _, right, _ = font.getbbox(value)
    return float(right - left)


def _format_datetime(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else "-"


def _stake_range(warning: PatrolWarning) -> str:
    if warning.start_stake == "-" and warning.end_stake == "-":
        return "-"
    return f"{warning.start_stake} - {warning.end_stake}"


def _route_text(warning: PatrolWarning) -> str:
    if warning.route_code and warning.route_name:
        return f"{warning.route_code} {warning.route_name}"
    return warning.route_code or warning.route_name or "-"


def _hours_between(start: datetime | None, end: datetime) -> int:
    if not start:
        return 0
    return max(0, int((end - start).total_seconds() // 3600))


def _remaining_hours(end_time: datetime | None, now: datetime, window_hours: int) -> int:
    if not end_time:
        return 0
    deadline = end_time + timedelta(hours=window_hours)
    if deadline <= now:
        return 0
    seconds = int((deadline - now).total_seconds())
    return (seconds + 3599) // 3600


def _status_text(end_time: datetime | None, now: datetime, window_hours: int) -> str:
    if not end_time or now < end_time:
        return "预警未结束"
    if now < end_time + timedelta(hours=window_hours):
        return "预警已结束"
    return "巡查结束"
