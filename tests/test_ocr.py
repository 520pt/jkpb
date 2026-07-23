from app.ocr import OcrText, _rapid_result_to_texts, build_review_grid, fallback_review_grid


def test_build_review_grid_from_positioned_ocr_text():
    texts = [
        OcrText(text="示例站7月排班表", x=100, y=10),
        OcrText(text="2025年7月", x=20, y=20),
        OcrText(text="1", x=150, y=55),
        OcrText(text="2", x=180, y=55),
        OcrText(text="姓名", x=90, y=85),
        OcrText(text="示例甲", x=90, y=120),
        OcrText(text="中", x=150, y=120),
        OcrText(text="晚", x=180, y=120),
    ]

    review = build_review_grid(texts, "uploads/month.png")

    assert review["year"] == 2025
    assert review["month"] == 7
    assert review["source_image_path"] == "uploads/month.png"
    assert review["grid"] == [{"name": "示例甲", "days": {"1": "中", "2": "晚"}}]


def test_fallback_review_grid_keeps_uploaded_image_path():
    review = fallback_review_grid("uploads/month.png")

    assert review["source_image_path"] == "uploads/month.png"
    assert review["grid"] == []
    assert 1 <= review["month"] <= 12


def test_rapid_result_to_texts_converts_boxes_to_centers():
    texts = _rapid_result_to_texts(
        [
            [
                [[100.0, 129.0], [130.0, 129.0], [130.0, 148.0], [100.0, 148.0]],
                "示例甲",
                0.99,
            ]
        ]
    )

    assert texts == [OcrText(text="示例甲", x=115.0, y=138.5, confidence=0.99)]


