from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.roster import normalize_shift_code


@dataclass(frozen=True)
class OcrText:
    text: str
    x: float
    y: float
    confidence: float = 1.0


@dataclass(frozen=True)
class CellMetrics:
    cell: Any
    fixed_label: str | None
    ink_fraction: float
    ink_height: int


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
        texts = _read_template_ocr_texts(path, template_result)
        if texts:
            _merge_template_ocr_texts(template_result, texts)
        return template_result
    return fallback_review_grid(str(path))


def _read_ocr_texts(path: Path) -> list[OcrText]:
    texts = _read_rapidocr_texts(path)
    if texts:
        return texts
    return _read_paddleocr_texts(path)


def _read_rapidocr_texts(path: Path) -> list[OcrText]:
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
    except Exception:
        return []

    try:
        ocr = RapidOCR()
        raw_result, _ = ocr(str(path))
    except Exception:
        return []
    return _rapid_result_to_texts(raw_result)


def _read_paddleocr_texts(path: Path) -> list[OcrText]:
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except Exception:
        return []

    try:
        ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
        raw_result = ocr.ocr(str(path), cls=True)
    except Exception:
        return []
    return _paddle_result_to_texts(raw_result)


def _read_template_ocr_texts(path: Path, template_result: dict[str, Any]) -> list[OcrText]:
    try:
        import cv2
    except Exception:
        return []

    image = cv2.imread(str(path))
    if image is None:
        return []

    grid = list(template_result.get("grid", []))
    if not grid:
        return []

    first_day_box = next((row.get("boxes", {}).get("1") for row in grid if row.get("boxes", {}).get("1")), None)
    row_boxes = [row.get("boxes", {}).get("1") for row in grid if row.get("boxes", {}).get("1")]
    if not first_day_box or not row_boxes:
        return []

    min_day_x = int(first_day_box["x"])
    y_min = min(int(box["y"]) for box in row_boxes)
    y_max = max(int(box["y"]) + int(box["height"]) for box in row_boxes)
    x_min = max(0, min_day_x - 180)
    x_max = min(image.shape[1], min_day_x)
    y_min = max(0, y_min)
    y_max = min(image.shape[0], y_max)
    if x_max <= x_min or y_max <= y_min:
        return []

    crop = image[y_min:y_max, x_min:x_max]
    return _read_rapidocr_crop_texts(crop, x_offset=x_min, y_offset=y_min, scale=2.0)


def _read_rapidocr_crop_texts(crop: Any, *, x_offset: int, y_offset: int, scale: float) -> list[OcrText]:
    try:
        import cv2
    except Exception:
        return []

    if crop is None or getattr(crop, "size", 0) == 0:
        return []

    resized = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        crop_path = Path(temp_dir) / "template-name-column.png"
        if not cv2.imwrite(str(crop_path), resized):
            return []
        texts = _read_rapidocr_texts(crop_path)

    return [
        OcrText(
            text=item.text,
            x=x_offset + item.x / scale,
            y=y_offset + item.y / scale,
            confidence=item.confidence,
        )
        for item in texts
    ]


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
    x_lines = _find_day_x_lines(dark, image=image)
    y_lines = _find_person_y_lines(dark)
    if len(x_lines) != 32 or len(y_lines) < 16:
        return None

    today = date.today()
    grid: list[dict[str, Any]] = []
    person_row_count = len(y_lines) - 1
    cell_metrics: list[CellMetrics] = []
    cell_refs: list[tuple[int, int]] = []
    for row_index in range(person_row_count):
        days: dict[str, str] = {}
        boxes: dict[str, dict[str, int]] = {}
        for day_index in range(31):
            x1, x2 = x_lines[day_index], x_lines[day_index + 1]
            y1, y2 = y_lines[row_index], y_lines[row_index + 1]
            cell = _crop_template_cell(image, x1, y1, x2 - x1, y2 - y1)
            cell_metrics.append(_measure_template_cell(cell))
            cell_refs.append((row_index, day_index + 1))
            days[str(day_index + 1)] = ""
            boxes[str(day_index + 1)] = {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}
        grid.append({"name": f"第{row_index + 1}行", "days": days, "boxes": boxes})

    labels = _classify_template_cell_metrics(cell_metrics)
    for (row_index, day), label in zip(cell_refs, labels):
        grid[row_index]["days"][str(day)] = label

    return {
        "year": today.year,
        "month": today.month,
        "source_image_path": str(path),
        "grid": grid,
        "ocr_status": "template_ok",
    }


def recheck_template_roster_cells(
    image_path: str | Path,
    current_grid: list[dict[str, Any]],
    *,
    year: int | None = None,
    month: int | None = None,
) -> dict[str, Any] | None:
    fresh = extract_template_roster_image(image_path)
    if fresh and fresh.get("grid"):
        if year and month:
            fresh["year"] = year
            fresh["month"] = month
            _trim_template_grid_to_month(list(fresh.get("grid", [])), year, month)
        return _diff_template_recheck_grid(current_grid, list(fresh.get("grid", [])))

    try:
        import cv2
    except Exception:
        return None

    path = Path(image_path)
    image = cv2.imread(str(path))
    if image is None:
        return None

    cell_metrics: list[CellMetrics] = []
    cell_refs: list[tuple[int, str]] = []
    for row_index, row in enumerate(current_grid):
        boxes = dict(row.get("boxes", {}))
        for day, raw_box in boxes.items():
            box = dict(raw_box or {})
            try:
                x = int(box["x"])
                y = int(box["y"])
                width = int(box["width"])
                height = int(box["height"])
            except (KeyError, TypeError, ValueError):
                continue
            cell = _crop_template_cell(image, x, y, width, height)
            if getattr(cell, "size", 0) == 0:
                continue
            cell_metrics.append(_measure_template_cell(cell))
            cell_refs.append((row_index, str(day)))

    if not cell_metrics:
        return None

    labels = _classify_template_cell_metrics(cell_metrics)
    corrected_grid = [
        {
            **row,
            "name": str(row.get("name") or ""),
            "days": dict(row.get("days", {})),
            "boxes": dict(row.get("boxes", {})),
        }
        for row in current_grid
    ]
    issues: list[dict[str, Any]] = []
    for (row_index, day), parsed_value in zip(cell_refs, labels):
        if row_index >= len(corrected_grid):
            continue
        current_days = corrected_grid[row_index]["days"]
        current_boxes = corrected_grid[row_index]["boxes"]
        current_value = str(current_days.get(day, ""))
        if current_value != parsed_value:
            issues.append(
                {
                    "row": row_index,
                    "day": day,
                    "before": current_value,
                    "after": parsed_value,
                    "box": current_boxes.get(day),
                }
            )
        current_days[day] = parsed_value

    if year and month:
        _trim_template_grid_to_month(corrected_grid, year, month)
        max_day = _month_day_count(year, month)
        issues = [issue for issue in issues if _is_valid_month_day(str(issue.get("day") or ""), max_day)]

    return {"grid": corrected_grid, "issues": issues}


def _diff_template_recheck_grid(current_grid: list[dict[str, Any]], parsed_grid: list[dict[str, Any]]) -> dict[str, Any]:
    corrected_grid: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for row_index, parsed_row in enumerate(parsed_grid):
        current_row = current_grid[row_index] if row_index < len(current_grid) else {}
        parsed_days = dict(parsed_row.get("days", {}))
        parsed_boxes = dict(parsed_row.get("boxes", {}))
        current_days = dict(current_row.get("days", {}))
        for day, parsed_value in parsed_days.items():
            current_value = str(current_days.get(day, ""))
            if current_value != str(parsed_value):
                issues.append(
                    {
                        "row": row_index,
                        "day": day,
                        "before": current_value,
                        "after": parsed_value,
                        "box": parsed_boxes.get(day),
                    }
                )
        corrected_grid.append(
            {
                **parsed_row,
                "name": str(current_row.get("name") or parsed_row.get("name") or ""),
                "days": parsed_days,
                "boxes": parsed_boxes,
            }
        )
    return {"grid": corrected_grid, "issues": issues}


def _crop_template_cell(image: Any, x: int, y: int, width: int, height: int) -> Any:
    margin_x = 2
    margin_y = 2
    return image[y + margin_y : y + height - margin_y, x + margin_x : x + width - margin_x]


def _merge_template_ocr_texts(template_result: dict[str, Any], texts: list[OcrText]) -> None:
    year, month = _detect_year_month(texts)
    template_result["year"] = year
    template_result["month"] = month

    grid = list(template_result.get("grid", []))
    if not grid:
        return
    _trim_template_grid_to_month(grid, year, month)
    first_day_box = next((row.get("boxes", {}).get("1") for row in grid if row.get("boxes", {}).get("1")), None)
    min_day_x = float(first_day_box["x"]) if first_day_box else 160.0
    row_centers: list[float] = []
    for row in grid:
        box = dict(row.get("boxes", {})).get("1")
        if box:
            row_centers.append(float(box["y"]) + float(box["height"]) / 2)

    for item in _detect_template_names(texts, min_day_x):
        row_index = _nearest_index(item.y, row_centers)
        if row_index is not None and row_index < len(grid):
            grid[row_index]["name"] = item.text.strip()


def _trim_template_grid_to_month(grid: list[dict[str, Any]], year: int, month: int) -> None:
    max_day = _month_day_count(year, month)
    for row in grid:
        days = dict(row.get("days", {}))
        boxes = dict(row.get("boxes", {}))
        row["days"] = {str(day): value for day, value in days.items() if _is_valid_month_day(str(day), max_day)}
        row["boxes"] = {str(day): value for day, value in boxes.items() if _is_valid_month_day(str(day), max_day)}


def _month_day_count(year: int, month: int) -> int:
    try:
        return calendar.monthrange(int(year), int(month))[1]
    except (TypeError, ValueError):
        return 31


def _is_valid_month_day(day: str, max_day: int) -> bool:
    if not day.isdigit():
        return False
    value = int(day)
    return 1 <= value <= max_day


def _detect_template_names(texts: list[OcrText], min_day_x: float) -> list[OcrText]:
    ignored = {"姓名", "序号", "时间", "工作", "天数", "排班表"}
    names: list[OcrText] = []
    for item in texts:
        text = item.text.strip()
        if text in ignored or normalize_shift_code(text) is not None:
            continue
        if not re.fullmatch(r"[\u4e00-\u9fff]{2,5}", text):
            continue
        if 40 <= item.x < min_day_x:
            names.append(item)
    return sorted(names, key=lambda item: item.y)


def _find_day_x_lines(dark: Any, image: Any | None = None) -> list[int]:
    import numpy as np

    counts = dark.sum(axis=0)
    candidates = _group_centers(np.where(counts > dark.shape[0] * 0.18)[0])
    fitted = _fit_regular_day_x_lines(candidates, counts, image_width=dark.shape[1])
    if fitted:
        return fitted

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

    if len(best) >= 32:
        windows = [best[index : index + 32] for index in range(len(best) - 31)]
        if image is not None:
            return max(windows, key=lambda window: (_score_day_line_window(image, window), window[0]))
        return windows[0]
    if len(best) >= 31:
        step = round(_median_gap(best))
        lines = best[:31]
        lines.append(lines[-1] + step)
        return lines
    return []


def _fit_regular_day_x_lines(candidates: list[int], counts: Any, *, image_width: int) -> list[int]:
    if len(candidates) < 32:
        return []

    import numpy as np

    best_score: tuple[int, int, float, int, int] | None = None
    best_lines: list[int] = []
    counts_array = np.asarray(counts)
    max_count = float(counts_array.max()) if counts_array.size else 0.0
    if max_count <= 0:
        return []
    strong_candidates = [value for value in candidates if counts_array[value] >= max_count * 0.72]

    for start in candidates:
        for step in range(18, 31):
            expected = [int(round(start + index * step)) for index in range(32)]
            if expected[-1] >= image_width:
                continue

            snapped: list[int] = []
            matched = 0
            darkness_score = 0.0
            for line in expected:
                left = max(0, line - 2)
                right = min(len(counts_array), line + 3)
                if right <= left:
                    snapped.append(line)
                    continue
                local_values = counts_array[left:right]
                local_offset = int(np.argmax(local_values))
                local_x = left + local_offset
                local_strength = float(local_values[local_offset]) / max_count
                if local_strength >= 0.72:
                    matched += 1
                darkness_score += local_strength - abs(local_x - line) * 0.02
                snapped.append(local_x)

            if matched < 28:
                continue
            next_candidates = [value for value in strong_candidates if value > snapped[-1]]
            right_clearance = min(next_candidates) - snapped[-1] if next_candidates else image_width - snapped[-1]
            score = (matched, min(right_clearance, step * 2), darkness_score, step, start)
            if best_score is None or score > best_score:
                best_score = score
                best_lines = snapped

    return best_lines


def _score_day_line_window(image: Any, lines: list[int]) -> float:
    import numpy as np

    if len(lines) < 32:
        return 0.0
    height = image.shape[0]
    y1 = max(0, int(height * 0.16))
    y2 = min(height, int(height * 0.85))

    def column_std(left: int, right: int) -> float:
        crop = image[y1:y2, left + 2 : right - 2]
        if getattr(crop, "size", 0) == 0:
            return 0.0
        return float(np.mean(crop.reshape(-1, 3).std(axis=0)))

    return min(column_std(lines[0], lines[1]), column_std(lines[-2], lines[-1]))


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
    return best if len(best) >= 16 else []


def _classify_template_cell(cell: Any) -> str:
    return _classify_template_cell_metrics([_measure_template_cell(cell)])[0]


def _measure_template_cell(cell: Any) -> CellMetrics:
    import cv2
    import numpy as np

    gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
    channel_max = np.max(cell, axis=2)
    channel_min = np.min(cell, axis=2)
    channel_range = channel_max - channel_min
    ink = (gray < 120) & (channel_max < 150) & (channel_range < 45)
    ink_fraction = float(ink.sum()) / float(ink.size)
    ys, _ = np.where(ink)
    ink_height = int(ys.max() - ys.min() + 1) if len(ys) else 0
    mean_bgr = cell.reshape(-1, 3).mean(axis=0)
    blue, green, red = mean_bgr

    if red > 180 and green > 180 and blue < 100:
        fixed_label = "休"
    elif ink_fraction < 0.035 or ink_height <= 4:
        fixed_label = ""
    elif blue > 200 and green > 200 and red > 200 and max(mean_bgr) - min(mean_bgr) < 30:
        fixed_label = "出差" if _looks_like_stacked_trip(ink) else ""
    else:
        fixed_label = None
    return CellMetrics(cell=cell, fixed_label=fixed_label, ink_fraction=ink_fraction, ink_height=ink_height)


def _looks_like_stacked_trip(ink: Any) -> bool:
    projection = ink.sum(axis=1)
    groups: list[tuple[int, int, int]] = []
    start: int | None = None
    for index, count in enumerate(projection):
        if count >= 1 and start is None:
            start = index
        elif count < 1 and start is not None:
            groups.append((start, index - 1, int(projection[start:index].sum())))
            start = None
    if start is not None:
        groups.append((start, len(projection) - 1, int(projection[start:].sum())))

    strong_groups = [(top, bottom, total) for top, bottom, total in groups if bottom - top >= 5 and total >= 20]
    if len(strong_groups) < 2:
        return False
    first, second = strong_groups[0], strong_groups[1]
    return first[1] < second[0] and second[0] - first[1] >= 2


def _classify_template_cell_metrics(metrics: list[CellMetrics]) -> list[str]:
    work_fractions = [item.ink_fraction for item in metrics if item.fixed_label is None]
    thresholds = _work_shift_thresholds(work_fractions)
    labels: list[str] = []
    for item in metrics:
        if item.fixed_label is not None:
            labels.append(item.fixed_label)
        elif item.ink_fraction < thresholds[0]:
            labels.append("中")
        elif item.ink_fraction < thresholds[1]:
            labels.append("早")
        else:
            labels.append("晚")
    return labels


def _work_shift_thresholds(fractions: list[float]) -> tuple[float, float]:
    if len(fractions) < 6:
        return (0.088, 0.108)

    centers = [min(fractions), _percentile(fractions, 0.5), max(fractions)]
    for _ in range(12):
        groups: list[list[float]] = [[], [], []]
        for value in fractions:
            index = min(range(3), key=lambda item: abs(value - centers[item]))
            groups[index].append(value)
        next_centers = [sum(group) / len(group) if group else centers[index] for index, group in enumerate(groups)]
        if all(abs(next_centers[index] - centers[index]) < 0.0001 for index in range(3)):
            break
        centers = next_centers

    centers = sorted(centers)
    if centers[2] - centers[0] < 0.015:
        return (0.088, 0.108)
    return ((centers[0] + centers[1]) / 2, (centers[1] + centers[2]) / 2)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * fraction)))
    return ordered[index]


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


def _rapid_result_to_texts(raw_result: Any) -> list[OcrText]:
    texts: list[OcrText] = []
    if not isinstance(raw_result, list):
        return texts

    for line in raw_result:
        try:
            box = line[0]
            text = line[1]
            score = line[2] if len(line) > 2 else 1.0
            xs = [point[0] for point in box]
            ys = [point[1] for point in box]
            texts.append(OcrText(text=str(text).strip(), x=sum(xs) / len(xs), y=sum(ys) / len(ys), confidence=float(score)))
        except Exception:
            continue
    return texts


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
