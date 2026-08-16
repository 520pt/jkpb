from __future__ import annotations

from io import BytesIO

from PIL import Image

from app.roster_import_image import render_roster_import_image


def test_roster_import_image_renders_all_people_and_many_diffs_without_cutting():
    grid = [
        {
            "name": f"人员{i:02d}",
            "days": {str(day): ("早" if day % 4 == 0 else "中" if day % 4 == 1 else "晚" if day % 4 == 2 else "休") for day in range(1, 32)},
        }
        for i in range(1, 17)
    ]
    diffs = [
        {"name": f"人员{(i % 16) + 1:02d}", "day": i + 1, "before": "出差", "after": "-"}
        for i in range(44)
    ]

    image_bytes = render_roster_import_image(
        {
            "success": False,
            "import_status": "conflict",
            "year": 2026,
            "month": 8,
            "grid": grid,
            "diffs": diffs,
            "issues": [],
            "reply": "2026年8月排班表已存在，发现 44 处差异。\n请按下方“下一步操作”回复。",
        }
    )

    image = Image.open(BytesIO(image_bytes))
    assert image.width == 900
    # 16 人 + 44 条差异应该生成长图，不能再按旧的“前 8 人/前 6 条差异”高度截断。
    assert image.height > 2200

