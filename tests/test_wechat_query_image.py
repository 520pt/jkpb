from app.wechat_query_image import render_wechat_query_image, _parse_next_reminder, _parse_person_monitor_range


def test_wechat_query_image_parses_person_monitor_range():
    reply = "商邱宏 2026-08-10 起 7 天监控排班\n- 2026-08-10 周一：中班\n- 2026-08-11 周二：晚班"

    assert _parse_person_monitor_range(reply) == [["2026-08-10", "周一", "中班"], ["2026-08-11", "周二", "晚班"]]
    image = render_wechat_query_image({"success": True, "query_type": "monitor_range", "reply": reply})

    assert image is not None
    assert image.startswith(b"\x89PNG")


def test_wechat_query_image_parses_person_next_reminder():
    reply = "商邱宏 下次提醒\n- 2026-08-10：请注意今天08:00至16:00是你的中班 记得写一二楼的卫生间消毒清洁记录"

    assert _parse_next_reminder(reply) == [["2026-08-10", "商邱宏", "请注意今天08:00至16:00是你的中班 记得写一二楼的卫生间消毒清洁记录"]]
    image = render_wechat_query_image({"success": True, "query_type": "next_reminder", "reply": reply})

    assert image is not None
    assert image.startswith(b"\x89PNG")