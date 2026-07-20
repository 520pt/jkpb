from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


WIDTH = 900
LEFT = 28
GAP = 14
HEADER_HEIGHT = 78
BLOCK_BOTTOM_PADDING = 18


def render_daily_duty_image(preview: dict[str, Any]) -> bytes:
    details = preview.get("details") or {}
    date_text = str(preview.get("send_at") or "")[:10]
    inner_width = WIDTH - LEFT * 2
    column_width = (inner_width - GAP * 2) // 3

    top_sections = [
        ("监控班", "#2563eb", [("早班", details.get("early") or "无"), ("中班", details.get("middle") or "无"), ("晚班", details.get("night") or "无")]),
        ("驾驶员", "#0f8a5f", [("大车", details.get("big_drivers") or "无"), ("小车", details.get("small_drivers") or "无")]),
        ("在岗", "#7c3aed", [("备勤人员", details.get("standby") or "无")]),
    ]
    rest_items = [
        ("今日下午休息", details.get("afternoon_rest") or "无"),
        ("正在休息", details.get("resting") or "无"),
        ("今日下午到岗", details.get("afternoon_return") or "无"),
    ]

    top_meta = []
    for title, accent, items in top_sections:
        item_meta = []
        for label, value in items:
            lines = _wrap_text(str(value), 16)
            item_meta.append({"label": label, "lines": lines, "height": max(76, 50 + len(lines) * 24)})
        top_meta.append({"title": title, "accent": accent, "items": item_meta, "height": 60 + sum(item["height"] for item in item_meta) + BLOCK_BOTTOM_PADDING})
    top_height = max(meta["height"] for meta in top_meta)

    rest_meta = []
    for label, value in rest_items:
        lines = _wrap_text(str(value), 13)
        rest_meta.append({"label": label, "lines": lines, "height": max(86, 55 + len(lines) * 24)})
    rest_card_height = max(item["height"] for item in rest_meta)
    rest_height = 60 + rest_card_height + BLOCK_BOTTOM_PADDING
    height = HEADER_HEIGHT + top_height + GAP + rest_height + 36

    image = Image.new("RGB", (WIDTH, height), "#f3f6fb")
    draw = ImageDraw.Draw(image)
    fonts = {
        "title": _font(22, bold=True),
        "header": _font(19, bold=True),
        "label": _font(15, bold=True),
        "body": _font(18, bold=True),
        "body_small": _font(17, bold=True),
        "muted": _font(18),
        "date": _font(15),
    }

    _rounded(draw, (LEFT, 18, WIDTH - LEFT, 64), 8, "#172033")
    draw.text((LEFT + 18, 32), "今日在岗人员", font=fonts["title"], fill="#ffffff")
    _rounded(draw, (WIDTH - LEFT - 132, 27, WIDTH - LEFT - 20, 55), 14, "#26364f")
    draw.text((WIDTH - LEFT - 116, 33), date_text, font=fonts["date"], fill="#dbeafe")

    for section_index, meta in enumerate(top_meta):
        x = LEFT + section_index * (column_width + GAP)
        y = HEADER_HEIGHT
        _rounded(draw, (x, y, x + column_width, y + top_height), 8, "#ffffff", "#d7deea")
        _rounded(draw, (x, y, x + column_width, y + 46), 8, meta["accent"])
        draw.rectangle((x, y + 36, x + column_width, y + 46), fill=meta["accent"])
        draw.text((x + 18, y + 13), meta["title"], font=fonts["header"], fill="#ffffff")
        item_y = y + 60
        for item in meta["items"]:
            _rounded(draw, (x + 16, item_y, x + column_width - 16, item_y + item["height"]), 8, "#f8fafc", "#e6ebf3")
            draw.text((x + 30, item_y + 12), item["label"], font=fonts["label"], fill=meta["accent"])
            _draw_lines(draw, item["lines"], x + 30, item_y + 42, fonts["body"] if "".join(item["lines"]) != "无" else fonts["muted"], "#18212f" if "".join(item["lines"]) != "无" else "#94a3b8", 25)
            item_y += item["height"]

    rest_y = HEADER_HEIGHT + top_height + GAP
    _rounded(draw, (LEFT, rest_y, LEFT + inner_width, rest_y + rest_height), 8, "#ffffff", "#d7deea")
    _rounded(draw, (LEFT, rest_y, LEFT + inner_width, rest_y + 46), 8, "#c2410c")
    draw.rectangle((LEFT, rest_y + 36, LEFT + inner_width, rest_y + 46), fill="#c2410c")
    draw.text((LEFT + 18, rest_y + 13), "休息状态", font=fonts["header"], fill="#ffffff")
    rest_card_width = (inner_width - 36 - GAP * 2) // 3
    for index, item in enumerate(rest_meta):
        x = LEFT + 18 + index * (rest_card_width + GAP)
        y = rest_y + 60
        _rounded(draw, (x, y, x + rest_card_width, y + rest_card_height), 8, "#fffaf7", "#f2d8ca")
        draw.text((x + 14, y + 12), item["label"], font=fonts["label"], fill="#9a3412")
        _draw_lines(draw, item["lines"], x + 14, y + 42, fonts["body_small"] if "".join(item["lines"]) != "无" else fonts["date"], "#18212f" if "".join(item["lines"]) != "无" else "#b08474", 24)

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _wrap_text(value: str, max_chars: int) -> list[str]:
    parts = [part for part in value.split("，") if part]
    lines: list[str] = []
    line = ""
    for part in parts:
        candidate = f"{line}，{part}" if line else part
        if len(candidate) > max_chars and line:
            lines.append(line)
            line = part
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines or ["无"]


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _candidate_font_paths(bold=bold):
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def has_cjk_font() -> bool:
    return any(path.exists() for path in _candidate_font_paths(bold=False))


def _candidate_font_paths(*, bold: bool) -> list[Path]:
    names = [
        "msyhbd.ttc" if bold else "msyh.ttc",
        "simhei.ttf",
        "NotoSansCJK-Regular.ttc",
        "NotoSansCJK-Bold.ttc" if bold else "NotoSansCJK-Regular.ttc",
        "NotoSansCJK-Regular.otf",
        "wqy-microhei.ttc",
    ]
    font_dirs = [
        Path("C:/Windows/Fonts"),
        Path("/usr/share/fonts/opentype/noto"),
        Path("/usr/share/fonts/truetype/noto"),
        Path("/usr/share/fonts/truetype/wqy"),
    ]
    paths: list[Path] = []
    for font_dir in font_dirs:
        for name in names:
            paths.append(font_dir / name)
    return paths


def _rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int, fill: str, outline: str | None = None) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline)


def _draw_lines(draw: ImageDraw.ImageDraw, lines: list[str], x: int, y: int, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, fill: str, line_height: int) -> None:
    for index, line in enumerate(lines):
        draw.text((x, y + index * line_height), line, font=font, fill=fill)
