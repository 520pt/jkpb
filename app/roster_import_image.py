from __future__ import annotations

import os
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


BG = "#f2f6fb"
CARD = "#ffffff"
INK = "#0f172a"
MUTED = "#64748b"
LINE = "#dbeafe"
BLUE = "#2563eb"
GREEN = "#059669"
ORANGE = "#d97706"
RED = "#dc2626"


def render_roster_import_image(result: dict[str, Any]) -> bytes:
    width = 900
    grid = list(result.get("grid") or [])
    diffs = list(result.get("diffs") or [])
    issues = list(result.get("issues") or [])
    status = str(result.get("import_status") or "")
    success = bool(result.get("success"))
    year = str(result.get("year") or "")
    month = str(result.get("month") or "")
    title = f"{year}年{month}月排班导入"
    status_text = _status_text(status, success)
    color = GREEN if success else (ORANGE if status == "conflict" else RED)

    rows = _preview_rows(grid)
    height = 310 + len(rows) * 44 + min(len(diffs), 6) * 34
    image = Image.new("RGB", (width, max(560, height)), BG)
    draw = ImageDraw.Draw(image)
    fonts = {
        "title": _font(36, bold=True),
        "sub": _font(22),
        "status": _font(28, bold=True),
        "body": _font(21),
        "small": _font(18),
    }

    draw.rounded_rectangle((24, 24, width - 24, 146), radius=24, fill=CARD)
    draw.text((54, 48), title, fill=INK, font=fonts["title"])
    draw.rounded_rectangle((54, 96, 210, 128), radius=16, fill=color)
    draw.text((76, 99), status_text, fill="#ffffff", font=fonts["small"])
    summary = f"识别 {len(grid)} 人｜核对问题 {len(issues)} 个"
    if status == "conflict":
        summary += f"｜差异 {len(diffs)} 处"
    draw.text((236, 99), summary, fill=MUTED, font=fonts["sub"])

    y = 168
    draw.rounded_rectangle((24, y, width - 24, y + 86), radius=20, fill=CARD)
    reply = str(result.get("reply") or "").strip()
    for line in _wrap_text(draw, reply, fonts["body"], width - 108)[:2]:
        draw.text((54, y + 18), line, fill=INK, font=fonts["body"])
        y += 28

    y = 278
    draw.rounded_rectangle((24, y, width - 24, y + 46 + len(rows) * 44), radius=20, fill=CARD)
    draw.text((54, y + 12), "识别预览（前 8 人）", fill=INK, font=fonts["sub"])
    table_y = y + 46
    headers = ["人员", "早班", "中班", "晚班", "休息"]
    xs = [54, 260, 390, 520, 650]
    for i, header in enumerate(headers):
        draw.text((xs[i], table_y + 10), header, fill=MUTED, font=fonts["small"])
    draw.line((54, table_y + 38, width - 54, table_y + 38), fill=LINE, width=2)
    y = table_y + 42
    for row in rows:
        for i, text in enumerate(row):
            draw.text((xs[i], y + 8), text, fill=INK, font=fonts["body"])
        y += 44

    if diffs:
        y += 18
        diff_rows = diffs[:6]
        draw.rounded_rectangle((24, y, width - 24, y + 58 + len(diff_rows) * 34), radius=20, fill=CARD)
        draw.text((54, y + 14), "重复月份差异预览", fill=ORANGE, font=fonts["sub"])
        y += 50
        for diff in diff_rows:
            text = f"{diff.get('name') or '-'} {diff.get('day') or '-'}日：{diff.get('before') or '-'} → {diff.get('after') or '-'}"
            draw.text((54, y), _ellipsis(draw, text, fonts["small"], width - 108), fill=INK, font=fonts["small"])
            y += 34

    return _png_bytes(image)


def _preview_rows(grid: list[dict[str, Any]]) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in grid[:8]:
        days = row.get("days") if isinstance(row.get("days"), dict) else {}
        counts = {"早": 0, "中": 0, "晚": 0, "休": 0}
        for value in days.values():
            text = str(value or "").strip()
            if text in counts:
                counts[text] += 1
        rows.append([str(row.get("name") or "-"), str(counts["早"]), str(counts["中"]), str(counts["晚"]), str(counts["休"])])
    return rows or [["暂无", "0", "0", "0", "0"]]


def _status_text(status: str, success: bool) -> str:
    if success:
        return "已导入"
    if status == "conflict":
        return "待确认覆盖"
    if status == "needs_names":
        return "需校对姓名"
    return "导入失败"


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for raw in str(text or "").splitlines() or [""]:
        current = ""
        for ch in raw:
            trial = current + ch
            if draw.textbbox((0, 0), trial, font=font)[2] <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = ch
        if current:
            lines.append(current)
    return lines or [""]


def _ellipsis(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    value = text
    while value and draw.textbbox((0, 0), value + "…", font=font)[2] > max_width:
        value = value[:-1]
    return value + "…"


@lru_cache(maxsize=8)
def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        os.getenv("DUTY_REMINDER_FONT", ""),
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if path and Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _png_bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
