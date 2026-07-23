from app.daily_duty_image import _font, _text_width, _wrap_text, render_daily_duty_image


def test_daily_duty_image_wraps_names_by_pixel_width():
    font = _font(18, bold=True)
    max_width = 120

    lines = _wrap_text("LongNameOne，LongNameTwo，LongNameThree", max_width, font)

    assert len(lines) >= 2
    assert all(_text_width(line, font) <= max_width for line in lines)


def test_daily_duty_image_renders_long_standby_names():
    image_bytes = render_daily_duty_image(
        {
            "send_at": "2026-07-22T07:50:00+08:00",
            "details": {
                "early": "示例甲",
                "middle": "示例乙",
                "night": "示例丙",
                "big_drivers": "示例乙，示例丁",
                "small_drivers": "示例戊，示例己",
                "standby": "示例庚，示例辛，示例壬，示例癸",
                "afternoon_rest": "无",
                "resting": "示例子，示例丑，示例寅，示例卯，示例辰",
                "afternoon_return": "无",
            },
        }
    )

    assert image_bytes.startswith(b"\x89PNG")
