from pathlib import Path

import cv2
import numpy as np

from app.ocr import (
    OcrText,
    _classify_template_cell,
    _find_day_x_lines,
    _find_person_y_lines,
    extract_roster_image,
    recheck_template_roster_cells,
)


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


def test_template_cell_classifier_does_not_treat_tall_single_white_text_as_trip():
    cell = np.full((29, 20, 3), 255, dtype=np.uint8)
    cv2.line(cell, (10, 1), (10, 27), (0, 0, 0), 1)
    cv2.line(cell, (6, 14), (14, 14), (0, 0, 0), 1)

    assert _classify_template_cell(cell) == ""


def test_template_cell_classifier_reads_sparse_stacked_trip_text():
    cell = np.full((29, 20, 3), 255, dtype=np.uint8)
    for y in (1, 5, 9, 11):
        cv2.line(cell, (7, y), (13, y), (0, 0, 0), 1)
    for x in (8, 12):
        cv2.line(cell, (x, 2), (x, 10), (0, 0, 0), 1)
    for y in (18, 22, 26):
        cv2.line(cell, (6, y), (15, y), (0, 0, 0), 1)
    for x in (9, 13):
        cv2.line(cell, (x, 17), (x, 27), (0, 0, 0), 1)

    assert _classify_template_cell(cell) == "出差"


def test_template_cell_classifier_reads_green_middle_cell():
    cell = np.full((29, 20, 3), (80, 170, 0), dtype=np.uint8)
    _draw_middle_strokes(cell, -2, -2)

    assert _classify_template_cell(cell) == "中"


def test_template_cell_classifier_reads_non_green_colored_middle_cell():
    cell = np.full((29, 20, 3), (96, 32, 140), dtype=np.uint8)
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


def test_template_parser_ignores_spurious_left_grid_line(tmp_path: Path):
    image_path = tmp_path / "roster.png"
    _write_synthetic_roster(image_path, row_count=16)
    image = cv2.imread(str(image_path))
    cv2.line(image, (137, 43), (137, 681), (0, 0, 0), 1)
    cv2.imwrite(str(image_path), image)

    result = extract_roster_image(image_path)

    assert result["grid"][0]["boxes"]["1"] == {"x": 161, "y": 120, "width": 24, "height": 33}
    assert result["grid"][0]["days"]["3"] == "休"


def test_template_parser_fits_day_lines_when_half_cell_noise_is_present(tmp_path: Path):
    image_path = tmp_path / "roster.png"
    _write_synthetic_roster(image_path, row_count=16)
    image = cv2.imread(str(image_path))
    expected = list(range(161, 906, 24))
    if expected[-1] != 905:
        expected.append(905)

    for x in expected[16:-1]:
        cv2.line(image, (x - 6, 120), (x - 6, 648), (0, 0, 0), 1)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    dark = gray < 80

    assert _find_day_x_lines(dark, image=image) == expected


def test_template_parser_fits_person_lines_when_any_row_has_trip_text_noise(tmp_path: Path):
    x_lines = list(range(161, 906, 24))
    if x_lines[-1] != 905:
        x_lines.append(905)
    y_lines = list(range(120, 120 + 17 * 33, 33))

    for trip_row in range(16):
        image_path = tmp_path / f"roster-trip-row-{trip_row}.png"
        _write_synthetic_roster(image_path, row_count=16)
        image = cv2.imread(str(image_path))
        for day in range(31):
            _paint_cell(image, x_lines[day], y_lines[trip_row], x_lines[day + 1], y_lines[trip_row + 1], "出差")
        cv2.imwrite(str(image_path), image)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        dark = gray < 80

        assert _find_person_y_lines(dark) == y_lines

        if trip_row not in {1, 7, 15}:
            continue

        result = extract_roster_image(image_path)

        assert result["grid"][trip_row]["days"]["1"] == "出差"
        assert result["grid"][trip_row]["boxes"]["1"] == {
            "x": 161,
            "y": y_lines[trip_row],
            "width": 24,
            "height": 33,
        }
        if trip_row + 1 < 16:
            assert result["grid"][trip_row + 1]["days"]["1"] == ""
            assert result["grid"][trip_row + 1]["boxes"]["1"] == {
                "x": 161,
                "y": y_lines[trip_row + 1],
                "width": 24,
                "height": 33,
            }


def test_person_lines_skip_merged_name_header_divider():
    image = np.full((727, 1114, 3), 255, dtype=np.uint8)
    x_lines = [159 + index * 25 for index in range(32)]
    y_lines = [98 + index * 32 for index in range(17)]

    # The exported layout has two header rows. The divider at y=68 exists in
    # the day area but does not cross the vertically merged serial/name header.
    for x in [28, 67, *x_lines]:
        cv2.line(image, (x, 38), (x, y_lines[-1]), (0, 0, 0), 1)
    cv2.line(image, (0, 38), (1080, 38), (0, 0, 0), 1)
    cv2.line(image, (159, 68), (1080, 68), (0, 0, 0), 1)
    cv2.line(image, (0, 98), (1080, 98), (0, 0, 0), 1)
    for y in y_lines[1:]:
        cv2.line(image, (0, y), (1080, y), (0, 0, 0), 1)

    dark = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) < 80

    assert _find_person_y_lines(dark) == y_lines


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
    assert {
        "row": 0,
        "day": "5",
        "before": "中",
        "after": "晚",
        "box": {"x": 257, "y": 120, "width": 24, "height": 33},
    } in result["issues"]


def test_template_recheck_repairs_stale_shifted_boxes(tmp_path: Path):
    image_path = tmp_path / "roster.png"
    _write_synthetic_roster(image_path, row_count=16)
    image = cv2.imread(str(image_path))
    cv2.line(image, (137, 43), (137, 681), (0, 0, 0), 1)
    cv2.imwrite(str(image_path), image)
    current_grid = [
        {
            "name": "示例甲",
            "days": {"3": "中"},
            "boxes": {"3": {"x": 185, "y": 120, "width": 24, "height": 33}},
        }
    ]

    result = recheck_template_roster_cells(image_path, current_grid)

    assert result is not None
    assert result["grid"][0]["days"]["3"] == "休"
    assert result["grid"][0]["boxes"]["3"] == {"x": 209, "y": 120, "width": 24, "height": 33}
    assert {
        "row": 0,
        "day": "3",
        "before": "中",
        "after": "休",
        "box": {"x": 209, "y": 120, "width": 24, "height": 33},
    } in result["issues"]


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
            OcrText(text="示例甲", x=105, y=136),
            OcrText(text="示例乙", x=105, y=169),
        ],
        raising=False,
    )

    result = extract_roster_image(image_path)

    assert result["ocr_status"] == "template_ok"
    assert len(result["grid"]) == 15
    assert result["grid"][0]["name"] == "示例甲"
    assert result["grid"][1]["name"] == "示例乙"


def test_template_parser_merges_split_name_column_ocr(tmp_path: Path, monkeypatch):
    image_path = tmp_path / "roster.png"
    _write_synthetic_roster(image_path)

    monkeypatch.setattr(
        "app.ocr._read_ocr_texts",
        lambda path: (_ for _ in ()).throw(AssertionError("template import must not call full-image OCR")),
        raising=False,
    )
    monkeypatch.setattr(
        "app.ocr._read_template_ocr_texts",
        lambda path, template_result: [
            OcrText(text="商", x=104, y=136),
            OcrText(text="邱宏", x=116, y=136),
            OcrText(text="罗", x=104, y=169),
            OcrText(text="富耀", x=116, y=169),
        ],
        raising=False,
    )

    result = extract_roster_image(image_path)

    assert result["ocr_status"] == "template_ok"
    assert len(result["grid"]) == 15
    assert result["grid"][0]["name"] == "商邱宏"
    assert result["grid"][1]["name"] == "罗富耀"


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
