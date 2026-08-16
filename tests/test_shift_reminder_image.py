from __future__ import annotations

from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

from PIL import Image

from app.reminders import ReminderEvent
from app.shift_reminder_image import render_shift_reminder_image
from app.wechat_query_image import render_wechat_query_image


def _opened_png(data: bytes) -> Image.Image:
    assert data.startswith(b"\x89PNG")
    return Image.open(BytesIO(data))


def test_rest_reminder_image_uses_dedicated_readable_layout():
    event = ReminderEvent(
        kind="rest",
        person_name="示例甲",
        send_at=datetime(2026, 8, 16, 7, 50, tzinfo=ZoneInfo("Asia/Shanghai")),
        content="示例甲 正在休息到 2026-08-18",
    )

    image = _opened_png(render_shift_reminder_image(event)).convert("RGB")

    assert image.size[0] == 900
    assert image.size[1] >= 560
    colors = image.getcolors(maxcolors=1_000_000) or []
    assert len(colors) > 20


def test_vacation_reminder_image_uses_dedicated_readable_layout():
    event = ReminderEvent(
        kind="vacation_end",
        person_name="示例甲",
        send_at=datetime(2026, 8, 18, 7, 50, tzinfo=ZoneInfo("Asia/Shanghai")),
        content="假期余额不足，今天下午就该返回站点了，加油天选打工人。",
    )

    image = _opened_png(render_shift_reminder_image(event)).convert("RGB")

    assert image.size[0] == 900
    assert image.size[1] >= 560
    colors = image.getcolors(maxcolors=1_000_000) or []
    assert len(colors) > 20


def test_rest_query_image_uses_structured_cards():
    data = render_wechat_query_image(
        {
            "success": True,
            "query_type": "rest_query",
            "person_name": "商邱宏",
            "target_date": "2026-08-16",
            "reply": "商邱宏 本月休息共8天，分2次休息，距离第一次休息还剩1天，从8月17日（周一）到8月20日（周四）",
            "details": {
                "total_days": 8,
                "rested_days": 0,
                "remaining_days": 8,
                "ranges": [
                    {"start": "2026-08-17", "end": "2026-08-20", "days": 4},
                    {"start": "2026-08-25", "end": "2026-08-28", "days": 4},
                ],
            },
        }
    )

    assert data is not None
    image = _opened_png(data).convert("RGB")
    assert image.size[0] == 900
    assert image.size[1] >= 520
