from app.daily_duty_image import _font, _text_width, _wrap_text, render_daily_duty_image


def test_daily_duty_image_wraps_names_by_pixel_width():
    font = _font(18, bold=True)
    max_width = 120

    lines = _wrap_text("罗森，罗越，张铭文，刘显坤", max_width, font)

    assert len(lines) >= 2
    assert all(_text_width(line, font) <= max_width for line in lines)


def test_daily_duty_image_renders_long_standby_names():
    image_bytes = render_daily_duty_image(
        {
            "send_at": "2026-07-22T07:50:00+08:00",
            "details": {
                "early": "李文杰",
                "middle": "赵光振",
                "night": "沐春宇",
                "big_drivers": "赵光振，杞文江",
                "small_drivers": "商邱宏，易国兵",
                "standby": "罗森，罗越，张铭文，刘显坤",
                "afternoon_rest": "无",
                "resting": "罗富耀，王德刚，杨伦，罗照云，陈刚",
                "afternoon_return": "无",
            },
        }
    )

    assert image_bytes.startswith(b"\x89PNG")
