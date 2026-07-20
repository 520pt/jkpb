from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.roster import normalize_shift_code

DEFAULT_PEOPLE = [
    "示例戊",
    "样例甲",
    "样例乙",
    "示例庚",
    "样例丙",
    "示例辛",
    "示例丁",
    "样例丁",
    "示例丙",
    "样例戊",
    "示例己",
    "示例甲",
    "示例乙",
    "样例己",
    "示例壬",
]


@dataclass(frozen=True)
class OcrText:
    text: str
    x: float
    y: float
    confidence: float = 1.0


def fallback_review_grid(source_image_path: str) -> dict[str, Any]:
    today = date.today()
    return {
        "year": today.year,
        "month": today.month,
        "source_image_path": source_image_path,
        "grid": [],
        "ocr_status": "unavailable",
    }


def build_review_grid(texts: list[OcrText], source_image_path: str) -> dict[str, Any]:
    year, month = _detect_year_month(texts)
    day_headers = _detect_day_headers(texts, year, month)
    names = _detect_names(texts, day_headers)
    grid = [{"name": item.text, "days": {}} for item in names]

    for item in texts:
        if normalize_shift_code(item.text) is None:
            continue
        row_index = _nearest_index(item.y, [name.y for name in names])
        day = _nearest_day(item.x, day_headers)
        if row_index is not None and day is not None:
            grid[row_index]["days"][str(day)] = item.text.strip()

    return {
        "year": year,
        "month": month,
        "source_image_path": source_image_path,
        "grid": grid,
        "ocr_status": "ok" if grid else "needs_review",
    }


def extract_roster_image(image_path: str | Path) -> dict[str, Any]:
    path = Path(image_path)
    template_result = extract_template_roster_image(path)
    if template_result is not None:
        return template_result

    try:
        from paddleocr import PaddleOCR  # type: ignore
    except Exception:
        return fallback_review_grid(str(path))

    try:
        ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
        raw_result = ocr.ocr(str(path), cls=True)
        texts = _paddle_result_to_texts(raw_result)
    except Exception:
        return fallback_review_grid(str(path))

    if not texts:
        return fallback_review_grid(str(path))
    return build_review_grid(texts, str(path))


def extract_template_roster_image(image_path: str | Path) -> dict[str, Any] | None:
    try:
        import cv2
        import numpy as np
    except Exception:
        return None

    path = Path(image_path)
    image = cv2.imread(str(path))
    if image is None:
        return None

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    dark = gray < 80
    x_lines = _find_day_x_lines(dark)
    y_lines = _find_person_y_lines(dark)
    if len(x_lines) != 32 or len(y_lines) != 16:
        return None

    today = date.today()
    grid: list[dict[str, Any]] = []
    for row_index in range(15):
        name = DEFAULT_PEOPLE[row_index] if row_index < len(DEFAULT_PEOPLE) else f"第{row_index + 1}行"
        days: dict[str, str] = {}
        boxes: dict[str, dict[str, int]] = {}
        for day_index in range(31):
            x1, x2 = x_lines[day_index], x_lines[day_index + 1]
            y1, y2 = y_lines[row_index], y_lines[row_index + 1]
            cell = image[y1 + 2 : y2 - 2, x1 + 2 : x2 - 2]
            days[str(day_index + 1)] = _classify_template_cell(cell)
            boxes[str(day_index + 1)] = {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}
        grid.append({"name": name, "days": days, "boxes": boxes})

    return {
        "year": today.year,
        "month": today.month,
        "source_image_path": str(path),
        "grid": grid,
        "ocr_status": "template_ok",
    }


def _find_day_x_lines(dark: Any) -> list[int]:
    import numpy as np

    counts = dark.sum(axis=0)
    candidates = _group_centers(np.where(counts > dark.shape[0] * 0.18)[0])
    best: list[int] = []
    for start in range(len(candidates)):
        sequence = [candidates[start]]
        for value in candidates[start + 1 :]:
            gap = value - sequence[-1]
            if 18 <= gap <= 30:
                sequence.append(value)
            elif gap > 35:
                break
        if len(sequence) > len(best):
            best = sequence

    if len(best) >= 31:
        step = round(_median_gap(best))
        lines = best[:31]
        lines.append(lines[-1] + step)
        return lines
    return []


def _find_person_y_lines(dark: Any) -> list[int]:
    import numpy as np

    counts = dark.sum(axis=1)
    candidates = _group_centers(np.where(counts > dark.shape[1] * 0.36)[0])
    best: list[int] = []
    for start in range(len(candidates)):
        sequence = [candidates[start]]
        for value in candidates[start + 1 :]:
            gap = value - sequence[-1]
            if 25 <= gap <= 40:
                sequence.append(value)
            elif gap > 45:
                break
        if len(sequence) > len(best):
            best = sequence
    return best[:16] if len(best) >= 16 else []


def _classify_template_cell(cell: Any) -> str:
    import cv2

    gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
    black_fraction = float((gray < 80).sum()) / float(gray.size)
    mean_bgr = cell.reshape(-1, 3).mean(axis=0)
    blue, green, red = mean_bgr

    if red > 180 and green > 180 and blue < 100:
        return "休"
    if black_fraction >= 0.145:
        return "出差"
    if black_fraction >= 0.108:
        return "晚"
    if black_fraction >= 0.088:
        return "早"
    if blue > 200 and green > 200 and red > 200 and max(mean_bgr) - min(mean_bgr) < 20:
        return ""
    return "中"


def _group_centers(values: Any) -> list[int]:
    if len(values) == 0:
        return []
    groups: list[int] = []
    start = previous = int(values[0])
    for raw_value in values[1:]:
        value = int(raw_value)
        if value <= previous + 1:
            previous = value
        else:
            groups.append((start + previous) // 2)
            start = previous = value
    groups.append((start + previous) // 2)
    return groups


def _median_gap(values: list[int]) -> float:
    gaps = sorted(values[index + 1] - values[index] for index in range(len(values) - 1))
    return float(gaps[len(gaps) // 2])


def _detect_year_month(texts: list[OcrText]) -> tuple[int, int]:
    joined = " ".join(item.text for item in texts)
    match = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月", joined)
    if match:
        return int(match.group(1)), int(match.group(2))
    month_match = re.search(r"(\d{1,2})\s*月", joined)
    today = date.today()
    return today.year, int(month_match.group(1)) if month_match else today.month


def _detect_day_headers(texts: list[OcrText], year: int, month: int) -> list[tuple[int, float]]:
    max_day = calendar.monthrange(year, month)[1]
    headers: list[tuple[int, float, float]] = []
    for item in texts:
        value = item.text.strip()
        if value.isdigit():
            day = int(value)
            if 1 <= day <= max_day:
                headers.append((day, item.x, item.y))
    if not headers:
        return [(day, float(day)) for day in range(1, max_day + 1)]

    header_y = min(item[2] for item in headers)
    filtered = [item for item in headers if abs(item[2] - header_y) <= 30]
    return [(day, x) for day, x, _ in sorted(filtered, key=lambda item: item[0])]


def _detect_names(texts: list[OcrText], day_headers: list[tuple[int, float]]) -> list[OcrText]:
    min_day_x = min((x for _, x in day_headers), default=140.0)
    ignored = {"姓名", "序号", "时间", "工作", "天数", "排班表"}
    names: list[OcrText] = []
    for item in texts:
        text = item.text.strip()
        if text in ignored or normalize_shift_code(text) is not None:
            continue
        if not re.fullmatch(r"[\u4e00-\u9fff]{2,5}", text):
            continue
        if item.x < min_day_x:
            names.append(item)
    return sorted(names, key=lambda item: item.y)


def _nearest_index(value: float, candidates: list[float]) -> int | None:
    if not candidates:
        return None
    distances = [(abs(value - candidate), index) for index, candidate in enumerate(candidates)]
    distance, index = min(distances)
    return index if distance <= 25 else None


def _nearest_day(value: float, day_headers: list[tuple[int, float]]) -> int | None:
    if not day_headers:
        return None
    distance, day = min((abs(value - x), day) for day, x in day_headers)
    return day if distance <= 25 else None


def _paddle_result_to_texts(raw_result: Any) -> list[OcrText]:
    lines: list[Any] = []
    if isinstance(raw_result, list):
        for item in raw_result:
            if isinstance(item, list) and item and isinstance(item[0], list) and len(item[0]) == 2:
                lines.append(item)
            elif isinstance(item, list):
                lines.extend(item)

    texts: list[OcrText] = []
    for line in lines:
        try:
            box = line[0]
            text, score = line[1]
            xs = [point[0] for point in box]
            ys = [point[1] for point in box]
            texts.append(OcrText(text=str(text).strip(), x=sum(xs) / len(xs), y=sum(ys) / len(ys), confidence=float(score)))
        except Exception:
            continue
    return texts


