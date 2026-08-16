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

    fonts = {
        "title": _font(34),
        "sub": _font(22),
        "status": _font(28, bold=True),
        "body": _font(21),
        "small": _font(18),
    }
    dummy = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    rows = _preview_rows(grid)
    reply_lines = _wrap_text(dummy, str(result.get("reply") or "").strip(), fonts["body"], width - 108)
    action_text = _action_text(result)
    action_lines = _wrap_text(dummy, action_text, fonts["body"], width - 108) if action_text else []
    diff_rows = diffs

    header_bottom = 146
    reply_card_h = max(86, 36 + len(reply_lines) * 30)
    table_card_h = 58 + 40 + len(rows) * 44
    action_card_h = (70 + len(action_lines) * 30) if action_lines else 0
    diff_card_h = (58 + len(diff_rows) * 36) if diff_rows else 0
    total_h = (
        24
        + (header_bottom - 24)
        + 22
        + reply_card_h
        + 24
        + table_card_h
        + (24 + action_card_h if action_card_h else 0)
        + (24 + diff_card_h if diff_card_h else 0)
        + 30
    )
    image = Image.new("RGB", (width, max(560, total_h)), BG)
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((24, 24, width - 24, 146), radius=24, fill=CARD)
    draw.text((54, 48), title, fill=INK, font=fonts["title"])
    draw.rounded_rectangle((54, 96, 210, 128), radius=16, fill=color)
    draw.text((76, 99), status_text, fill="#ffffff", font=fonts["small"])
    summary = f"识别 {len(grid)} 人｜核对问题 {len(issues)} 个"
    if status == "conflict":
        summary += f"｜差异 {len(diffs)} 处"
    draw.text((236, 99), summary, fill=MUTED, font=fonts["sub"])

    y = 168
    draw.rounded_rectangle((24, y, width - 24, y + reply_card_h), radius=20, fill=CARD)
    line_y = y + 18
    for line in reply_lines:
        draw.text((54, line_y), line, fill=INK, font=fonts["body"])
        line_y += 30

    y += reply_card_h + 24
    draw.rounded_rectangle((24, y, width - 24, y + table_card_h), radius=20, fill=CARD)
    draw.text((54, y + 14), f"识别统计（共 {len(grid)} 人）", fill=INK, font=fonts["sub"])
    table_y = y + 58
    headers = ["人员", "早班", "中班", "晚班", "休息"]
    xs = [54, 260, 390, 520, 650]
    for i, header in enumerate(headers):
        draw.text((xs[i], table_y + 10), header, fill=MUTED, font=fonts["small"])
    draw.line((54, table_y + 38, width - 54, table_y + 38), fill=LINE, width=2)
    y = table_y + 42
    for row in rows:
        for i, text in enumerate(row):
            value = _ellipsis(draw, text, fonts["body"], 174 if i == 0 else 90)
            draw.text((xs[i], y + 8), value, fill=INK, font=fonts["body"])
        y += 44

    if action_lines:
        y += 24
        draw.rounded_rectangle((24, y, width - 24, y + action_card_h), radius=20, fill="#fff7ed", outline="#fed7aa")
        draw.text((54, y + 14), "下一步操作", fill=ORANGE, font=fonts["sub"])
        y += 50
        for line in action_lines:
            draw.text((54, y), line, fill=INK, font=fonts["body"])
            y += 30

    if diff_rows:
        y += 24
        draw.rounded_rectangle((24, y, width - 24, y + diff_card_h), radius=20, fill=CARD)
        draw.text((54, y + 14), "重复月份差异预览", fill=ORANGE, font=fonts["sub"])
        y += 50
        for diff in diff_rows:
            text = f"{diff.get('name') or '-'} {diff.get('day') or '-'}日：{diff.get('before') or '-'} → {diff.get('after') or '-'}"
            draw.text((54, y), _ellipsis(draw, text, fonts["small"], width - 108), fill=INK, font=fonts["small"])
            y += 36

    return _png_bytes(image)


def _preview_rows(grid: list[dict[str, Any]]) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in grid:
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


def _action_text(result: dict[str, Any]) -> str:
    configured = str(result.get("action_text") or "").strip()
    if configured:
        return configured
    if str(result.get("import_status") or "") != "conflict":
        return ""
    return (
        "1. 覆盖现有排班：回复 1 / 覆盖导入 / 确认覆盖 / 覆盖 / 确认导入 / 导入\n"
        "2. 取消本次导入：回复 2 / 取消导入 / 取消 / 放弃\n"
        "提示：5 分钟内有效，过期后需要重新点击“导入排班”并发送图片。"
    )


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
