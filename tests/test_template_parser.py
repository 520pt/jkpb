from pathlib import Path

import cv2
import numpy as np

from app.ocr import extract_roster_image


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


def _write_synthetic_roster(path: Path) -> None:
    image = np.full((731, 1089, 3), 255, dtype=np.uint8)
    x_lines = list(range(161, 906, 24))
    if x_lines[-1] != 905:
        x_lines.append(905)
    y_lines = list(range(120, 616, 33))

    for x in [28, 67, 161, *x_lines]:
        cv2.line(image, (x, 43), (x, 615), (0, 0, 0), 1)
    for y in [43, 76, *y_lines]:
        cv2.line(image, (0, y), (1080, y), (0, 0, 0), 1)

    patterns = ["", "中", "休", "早", "晚", "出差"]
    for row in range(15):
        for day in range(31):
            pattern = patterns[day] if row == 0 and day < len(patterns) else ""
            x1, x2 = x_lines[day], x_lines[day + 1]
            y1, y2 = y_lines[row], y_lines[row + 1]
            _paint_cell(image, x1, y1, x2, y2, pattern)

    cv2.imwrite(str(path), image)


def _paint_cell(image: np.ndarray, x1: int, y1: int, x2: int, y2: int, value: str) -> None:
    if value == "休":
        image[y1 + 1 : y2, x1 + 1 : x2] = (0, 255, 255)
        black_pixels = 49
    elif value in {"早", "晚"}:
        image[y1 + 1 : y2, x1 + 1 : x2] = (80, 170, 0)
        black_pixels = 55 if value == "早" else 70
    elif value == "中":
        image[y1 + 1 : y2, x1 + 1 : x2] = (80, 170, 0)
        black_pixels = 46
    elif value == "出差":
        black_pixels = 95
    else:
        black_pixels = 46

    x = x1 + 4
    y = y1 + 4
    painted = 0
    while painted < black_pixels:
        image[y, x] = (0, 0, 0)
        painted += 1
        x += 1
        if x >= x2 - 4:
            x = x1 + 4
            y += 1
