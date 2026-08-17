from __future__ import annotations

import re
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image, ImageOps


DEFAULT_CONSTRUCTION_LOCATION = "南涧至景东方向K88+730-K88+880上挡墙施工安全检查"


def build_construction_image_docx(
    *,
    template_path: Path,
    output_path: Path,
    location: str,
    image_paths: list[Path],
) -> None:
    if len(image_paths) != 2:
        raise ValueError("施工图片需要 2 张图片")
    if not template_path.is_file():
        raise FileNotFoundError(f"施工图片 Word 模板不存在：{template_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    replacements = {
        "word/media/image1.jpeg": _image_to_jpeg_bytes(image_paths[0], ratio=5974080 / 3182620),
        "word/media/image2.jpeg": _image_to_jpeg_bytes(image_paths[1], ratio=5974080 / 3896360),
    }
    with zipfile.ZipFile(template_path) as src, zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as dst:
        written: set[str] = set()
        for info in src.infolist():
            if info.filename in written:
                continue
            data = replacements.get(info.filename)
            if data is None:
                data = src.read(info.filename)
                if info.filename == "word/document.xml":
                    data = _replace_location_text(data, location)
            dst.writestr(info, data)
            written.add(info.filename)


def _replace_location_text(xml_bytes: bytes, location: str) -> bytes:
    clean_location = str(location or "").strip() or DEFAULT_CONSTRUCTION_LOCATION
    if DEFAULT_CONSTRUCTION_LOCATION.encode("utf-8") in xml_bytes:
        return xml_bytes.replace(DEFAULT_CONSTRUCTION_LOCATION.encode("utf-8"), clean_location.encode("utf-8"))
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return xml_bytes
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    changed = False
    for node in root.findall(".//w:t", ns):
        value = node.text or ""
        if DEFAULT_CONSTRUCTION_LOCATION in value:
            node.text = value.replace(DEFAULT_CONSTRUCTION_LOCATION, clean_location)
            changed = True
    if not changed:
        texts = list(root.findall(".//w:t", ns))
        for idx in (2, 5):
            if idx < len(texts):
                texts[idx].text = clean_location
    ET.register_namespace("w", "http://schemas.openxmlformats.org/wordprocessingml/2006/main")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _image_to_jpeg_bytes(path: Path, *, ratio: float) -> bytes:
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    ratio = ratio if ratio > 0 else image.width / max(1, image.height)
    width = 1600
    height = max(1, round(width / ratio))
    scale = min(width / image.width, height / image.height)
    resized = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (width, height), (255, 255, 255))
    canvas.paste(resized, ((width - resized.width) // 2, (height - resized.height) // 2))
    output = BytesIO()
    canvas.save(output, format="JPEG", quality=92, optimize=True)
    return output.getvalue()


def construction_docx_contains_location(path: Path, location: str) -> bool:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
    return re.sub(r"\s+", "", str(location or "")) in re.sub(r"\s+", "", xml)
