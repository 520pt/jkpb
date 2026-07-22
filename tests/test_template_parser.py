from pathlib import Path

import cv2
import numpy as np

from app.ocr import OcrText, _classify_template_cell, extract_roster_image, recheck_template_roster_cells


def test_template_parser_reads_fixed_roster_grid(tmp_path: Path):
    image_path = tmp_path / "roster.png"
    _write_synthetic_roster(image_path)

    result = extract_roster_image(image_path)

    assert result["ocr_status"] == "template_ok"
    assert len(result["grid"]) == 15
    assert len(result["grid"][0]["days"]) == 31
    assert result["grid"][0]["days"]["1"] == ""
    assert result["grid"][0]["days"]["2"] == "中"
    assert result["grid"][0]["days"]["3"] == "休"
    assert result["grid"][0]["days"]["4"] == "早"
    assert result["grid"][0]["days"]["5"] == "晚"
    assert result["grid"][0]["days"]["6"] == "出差"
    assert result["grid"][0]["boxes"]["1"] == {"x": 161, "y": 120, "width": 24, "height": 33}


def test_template_cell_classifier_ignores_white_middle_cell():
    cell = np.full((29, 20, 3), 255, dtype=np.uint8)
    _draw_middle_strokes(cell, -2, -2)

    assert _classify_template_cell(cell) == ""


def test_template_cell_classifier_reads_green_middle_cell():
    cell = np.full((29, 20, 3), (80, 170, 0), dtype=np.uint8)
    _draw_middle_strokes(cell, -2, -2)

    assert _classify_template_cell(cell) == "中"


def test_template_parser_reads_sixteen_person_roster_grid(tmp_path: Path):
    image_path = tmp_path / "roster.png"
    _write_synthetic_roster(image_path, row_count=16)

    result = extract_roster_image(image_path)

    assert result["ocr_status"] == "template_ok"
    assert len(result["grid"]) == 16
    assert len(result["grid"][15]["days"]) == 31
    assert result["grid"][15]["boxes"]["1"] == {"x": 161, "y": 615, "width": 24, "height": 33}


def test_template_recheck_uses_existing_cell_boxes(tmp_path: Path):
    image_path = tmp_path / "roster.png"
    _write_synthetic_roster(image_path)
    current_grid = [
        {
            "name": "示例甲",
            "days": {"5": "中"},
            "boxes": {"5": {"x": 257, "y": 120, "width": 24, "height": 33}},
        }
    ]

    result = recheck_template_roster_cells(image_path, current_grid)

    assert result is not None
    assert result["grid"][0]["days"]["5"] == "晚"
    assert result["issues"] == [
        {
            "row": 0,
            "day": "5",
            "before": "中",
            "after": "晚",
            "box": {"x": 257, "y": 120, "width": 24, "height": 33},
        }
    ]


def test_template_parser_does_not_show_sample_names_when_ocr_is_unavailable(tmp_path: Path):
    image_path = tmp_path / "roster.png"
    _write_synthetic_roster(image_path)

    result = extract_roster_image(image_path)

    assert result["grid"][0]["name"] == "第1行"
    assert result["grid"][1]["name"] == "第2行"


def test_template_parser_merges_name_column_ocr_without_full_image_ocr(tmp_path: Path, monkeypatch):
    image_path = tmp_path / "roster.png"
    _write_synthetic_roster(image_path)

    def fail_if_called(path: Path):
        raise AssertionError("template import must not call full-image OCR")

    monkeypatch.setattr(
        "app.ocr._read_ocr_texts",
        fail_if_called,
        raising=False,
    )
    monkeypatch.setattr(
        "app.ocr._read_template_ocr_texts",
        lambda path, template_result: [
            OcrText(text="罗森", x=105, y=136),
            OcrText(text="李金雷", x=105, y=169),
        ],
        raising=False,
    )

    result = extract_roster_image(image_path)

    assert result["ocr_status"] == "template_ok"
    assert len(result["grid"]) == 15
    assert result["grid"][0]["name"] == "罗森"
    assert result["grid"][1]["name"] == "李金雷"


def test_non_template_image_does_not_fall_back_to_ocr(tmp_path: Path, monkeypatch):
    image_path = tmp_path / "blank.png"
    image = np.full((120, 200, 3), 255, dtype=np.uint8)
    cv2.imwrite(str(image_path), image)

    def fail_if_called(path: Path):
        raise AssertionError("non-template import must not call OCR")

    monkeypatch.setattr(
        "app.ocr._read_ocr_texts",
        fail_if_called,
        raising=False,
    )

    result = extract_roster_image(image_path)

    assert result["ocr_status"] == "unavailable"
    assert result["grid"] == []


def _write_synthetic_roster(path: Path, row_count: int = 15) -> None:
    image = np.full((731, 1089, 3), 255, dtype=np.uint8)
    x_lines = list(range(161, 906, 24))
    if x_lines[-1] != 905:
        x_lines.append(905)
    y_lines = list(range(120, 120 + (row_count + 1) * 33, 33))

    for x in [28, 67, 161, *x_lines]:
        cv2.line(image, (x, 43), (x, y_lines[-1]), (0, 0, 0), 1)
    for y in [43, 76, *y_lines]:
        cv2.line(image, (0, y), (1080, y), (0, 0, 0), 1)

    patterns = ["", "中", "休", "早", "晚", "出差"]
    for row in range(row_count):
        for day in range(31):
            pattern = patterns[day] if row == 0 and day < len(patterns) else ""
            x1, x2 = x_lines[day], x_lines[day + 1]
            y1, y2 = y_lines[row], y_lines[row + 1]
            _paint_cell(image, x1, y1, x2, y2, pattern)

    cv2.imwrite(str(path), image)


def _paint_cell(image: np.ndarray, x1: int, y1: int, x2: int, y2: int, value: str) -> None:
    if value == "休":
        image[y1 + 1 : y2, x1 + 1 : x2] = (0, 255, 255)
        _draw_cross_strokes(image, x1, y1)
    elif value in {"早", "晚"}:
        image[y1 + 1 : y2, x1 + 1 : x2] = (80, 170, 0)
        _draw_early_strokes(image, x1, y1)
        if value == "晚":
            _draw_late_extra_strokes(image, x1, y1)
    elif value == "中":
        image[y1 + 1 : y2, x1 + 1 : x2] = (80, 170, 0)
        _draw_middle_strokes(image, x1, y1)
    elif value == "出差":
        _draw_trip_strokes(image, x1, y1)


def _draw_middle_strokes(image: np.ndarray, x1: int, y1: int) -> None:
    cv2.rectangle(image, (x1 + 7, y1 + 10), (x1 + 15, y1 + 18), (0, 0, 0), 1)
    cv2.line(image, (x1 + 11, y1 + 8), (x1 + 11, y1 + 22), (0, 0, 0), 1)


def _draw_cross_strokes(image: np.ndarray, x1: int, y1: int) -> None:
    cv2.line(image, (x1 + 6, y1 + 9), (x1 + 4, y1 + 22), (0, 0, 0), 1)
    cv2.line(image, (x1 + 10, y1 + 10), (x1 + 17, y1 + 20), (0, 0, 0), 1)
    cv2.line(image, (x1 + 13, y1 + 8), (x1 + 13, y1 + 23), (0, 0, 0), 1)


def _draw_early_strokes(image: np.ndarray, x1: int, y1: int) -> None:
    cv2.rectangle(image, (x1 + 7, y1 + 8), (x1 + 15, y1 + 15), (0, 0, 0), 1)
    cv2.line(image, (x1 + 8, y1 + 12), (x1 + 14, y1 + 12), (0, 0, 0), 1)
    cv2.line(image, (x1 + 5, y1 + 20), (x1 + 17, y1 + 20), (0, 0, 0), 1)
    cv2.line(image, (x1 + 11, y1 + 15), (x1 + 11, y1 + 23), (0, 0, 0), 1)


def _draw_late_extra_strokes(image: np.ndarray, x1: int, y1: int) -> None:
    cv2.line(image, (x1 + 4, y1 + 9), (x1 + 4, y1 + 22), (0, 0, 0), 1)
    cv2.line(image, (x1 + 18, y1 + 9), (x1 + 16, y1 + 23), (0, 0, 0), 1)
    cv2.line(image, (x1 + 15, y1 + 18), (x1 + 19, y1 + 22), (0, 0, 0), 1)


def _draw_trip_strokes(image: np.ndarray, x1: int, y1: int) -> None:
    for offset_y in (4, 17):
        cv2.rectangle(image, (x1 + 5, y1 + offset_y), (x1 + 16, y1 + offset_y + 8), (0, 0, 0), 1)
        cv2.line(image, (x1 + 4, y1 + offset_y + 10), (x1 + 18, y1 + offset_y + 10), (0, 0, 0), 1)
