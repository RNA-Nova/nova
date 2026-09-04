"""图片处理（深度``processImage`` 语义）。

管线：格式归一（bmp 等 API 普遍不接受的格式 → PNG）→ EXIF 方向校正 →
维度/字节预算压缩链（2000x2000 维度限 + base64 后 ≤4.5MB 预算：
PNG/JPEG 择优 → JPEG 质量递减 → 尺寸 0.75 倍递减至 1x1）。
无法解码或压不进预算时返回 ``ok=False`` + 提示文案：提示文本给模型继续，不标工具错误。
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# 单边最大尺寸
MAX_IMAGE_DIMENSION = 2000

# base64 编码后的字节预算（4.5MB，
# 低于 Anthropic 5MB 限制留余量）
MAX_IMAGE_BASE64_BYTES = int(4.5 * 1024 * 1024)

# JPEG 质量递减步（[80, 85, 70, 55, 40]）
_JPEG_QUALITY_STEPS = (80, 85, 70, 55, 40)

# 尺寸递减系数（每轮 0.75 倍直至 1x1）
_DIMENSION_SHRINK_FACTOR = 0.75

# 提示文案
_MESSAGE_UNSUPPORTED_FORMAT = (
    "[Image omitted: could not be converted to a supported inline image format.]"
)
_MESSAGE_OVER_SIZE_LIMIT = (
    "[Image omitted: could not be resized below the inline image size limit.]"
)

# API 普遍接受的内联图片格式（# jpg 归一为 jpeg）
_SUPPORTED_MIME_TYPES = {
    "image/png": "image/png",
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/gif": "image/gif",
    "image/webp": "image/webp",
}


@dataclass
class ProcessedImage:
    """process_image 结果。

    ``ok=False`` 时仅 ``message`` 有效：提示文案返回给模型继续，
    不构成工具错误。
    """

    ok: bool
    data: bytes = b""
    mime_type: str = ""
    hints: List[str] = field(default_factory=list)
    resized: bool = False
    width: int = 0
    height: int = 0
    original_width: int = 0
    original_height: int = 0
    message: str = ""


def _base_mime_type(mime_type: str) -> str:
    """去掉参数部分并小写。"""
    return mime_type.split(";")[0].strip().lower()


def _b64_size(data: bytes) -> int:
    """base64 编码后的字节数（含 padding，与实际编码结果等长）。"""
    return ((len(data) + 2) // 3) * 4


def _js_round(value: float) -> int:
    """对齐 JS ``Math.round``（正数等价于 floor(x + 0.5)，避开 Python 银行家舍入）。"""
    return math.floor(value + 0.5)


def _encode_png(img: "object") -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def _encode_jpeg(img: "object", quality: int) -> bytes:
    # JPEG 不支持透明度/调色板模式，先转 RGB
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def _convert_to_png(data: bytes) -> Optional[bytes]:
    """不支持的格式转 PNG（含 EXIF 方向校正）。"""
    from PIL import Image, ImageOps  # 延迟导入：文本读取路径不付出 PIL 加载成本

    try:
        with Image.open(io.BytesIO(data)) as img:
            transposed = ImageOps.exif_transpose(img)
            transposed.load()
            return _encode_png(transposed)
    except Exception:
        return None


def _conversion_hints(converted_from: Optional[str], to_mime: str) -> List[str]:
    """格式转换提示。"""
    if not converted_from or converted_from == to_mime:
        return []
    return [f"[Image converted from {converted_from} to {to_mime}.]"]


def _dimension_note(
    original_width: int, original_height: int, width: int, height: int
) -> str:
    """缩放坐标映射系数提示。"""
    scale = original_width / width
    return (
        f"[Image: original {original_width}x{original_height}, "
        f"displayed at {width}x{height}. Multiply coordinates by "
        f"{scale:.2f} to map to original image.]"
    )


def process_image(
    data: bytes,
    mime_type: str,
    resize: bool = True,
    *,
    max_dimension: int = MAX_IMAGE_DIMENSION,
    max_bytes: int = MAX_IMAGE_BASE64_BYTES,
) -> ProcessedImage:
    """处理图片。

    1. 格式归一：png/jpeg/gif/webp 原样通过（jpg 归一为 jpeg），其余格式
       （bmp 等）转 PNG 并记转换提示；
    2. ``resize`` 为真时做 EXIF 方向校正（竖拍照片方向正确），并按
       2000x2000 维度限 + base64 ≤4.5MB 预算链压缩；
    3. 输出 hints：格式转换提示 + 缩放坐标映射系数提示。
    """
    from PIL import Image, ImageOps  # 延迟导入：文本读取路径不付出 PIL 加载成本

    # 1. 格式归一
    normalized_mime = _SUPPORTED_MIME_TYPES.get(_base_mime_type(mime_type))
    converted_from: Optional[str] = None
    if normalized_mime is not None:
        norm_data, norm_mime = data, normalized_mime
    else:
        png_data = _convert_to_png(data)
        if png_data is None:
            return ProcessedImage(ok=False, message=_MESSAGE_UNSUPPORTED_FORMAT)
        converted_from = _base_mime_type(mime_type)
        norm_data, norm_mime = png_data, "image/png"

    if not resize:
        # auto_resize 关闭：归一后原样返回（此路径不解码）；
        # 尺寸尝试量取供展示，坏字节量不到不视为失败
        width = height = 0
        try:
            with Image.open(io.BytesIO(norm_data)) as img:
                width, height = img.size
        except Exception:
            pass
        return ProcessedImage(
            ok=True,
            data=norm_data,
            mime_type=norm_mime,
            hints=_conversion_hints(converted_from, norm_mime),
            resized=False,
            width=width,
            height=height,
            original_width=width,
            original_height=height,
        )

    # 2. EXIF 方向校正
    try:
        with Image.open(io.BytesIO(norm_data)) as img:
            image = ImageOps.exif_transpose(img)
            image.load()
    except Exception:
        # 无法解码
        return ProcessedImage(ok=False, message=_MESSAGE_OVER_SIZE_LIMIT)

    original_width, original_height = image.size

    # 3. 已在全部限制内：原样返回（字节不动，宽高取 EXIF 校正后）
    if (
        original_width <= max_dimension
        and original_height <= max_dimension
        and _b64_size(norm_data) < max_bytes
    ):
        return ProcessedImage(
            ok=True,
            data=norm_data,
            mime_type=norm_mime,
            hints=_conversion_hints(converted_from, norm_mime),
            resized=False,
            width=original_width,
            height=original_height,
            original_width=original_width,
            original_height=original_height,
        )

    # 4. 首轮 clamp 到 max_dimension 内（保比例）
    target_width, target_height = original_width, original_height
    if target_width > max_dimension:
        target_height = _js_round(target_height * max_dimension / target_width)
        target_width = max_dimension
    if target_height > max_dimension:
        target_width = _js_round(target_width * max_dimension / target_height)
        target_height = max_dimension

    # 5. 预算链：PNG/JPEG 择优 → JPEG 质量递减 → 尺寸 0.75 倍递减至 1x1
    current_width, current_height = target_width, target_height
    while True:
        resized_image = image.resize((current_width, current_height), Image.LANCZOS)
        candidates: List[Tuple[bytes, str]] = [
            (_encode_png(resized_image), "image/png")
        ]
        for quality in _JPEG_QUALITY_STEPS:
            candidates.append((_encode_jpeg(resized_image, quality), "image/jpeg"))
        for candidate_data, candidate_mime in candidates:
            if _b64_size(candidate_data) < max_bytes:
                return ProcessedImage(
                    ok=True,
                    data=candidate_data,
                    mime_type=candidate_mime,
                    hints=_conversion_hints(converted_from, candidate_mime)
                    + [
                        _dimension_note(
                            original_width,
                            original_height,
                            current_width,
                            current_height,
                        )
                    ],
                    resized=True,
                    width=current_width,
                    height=current_height,
                    original_width=original_width,
                    original_height=original_height,
                )

        if current_width == 1 and current_height == 1:
            break
        next_width = (
            1
            if current_width == 1
            else max(1, math.floor(current_width * _DIMENSION_SHRINK_FACTOR))
        )
        next_height = (
            1
            if current_height == 1
            else max(1, math.floor(current_height * _DIMENSION_SHRINK_FACTOR))
        )
        if next_width == current_width and next_height == current_height:
            break
        current_width, current_height = next_width, next_height

    # 压不进预算
    return ProcessedImage(ok=False, message=_MESSAGE_OVER_SIZE_LIMIT)


__all__ = [
    "MAX_IMAGE_BASE64_BYTES",
    "MAX_IMAGE_DIMENSION",
    "ProcessedImage",
    "process_image",
]
