"""图片处理管线测试。"""

import base64
import io
import random

from nova_coding_agent.tools_common.image import (
    MAX_IMAGE_BASE64_BYTES,
    process_image,
)
from PIL import Image

# EXIF Orientation 标签
_EXIF_ORIENTATION_TAG = 0x0112


def _make_image_bytes(
    width,
    height,
    fmt="PNG",
    color=(200, 30, 30),
    exif_orientation=None,
    noise=False,
):
    """构造测试图片字节；noise=True 时用确定性随机像素（PNG 压缩不动）。"""
    if noise:
        rng = random.Random(42)
        img = Image.frombytes("RGB", (width, height), rng.randbytes(width * height * 3))
    else:
        img = Image.new("RGB", (width, height), color)
    buffer = io.BytesIO()
    save_kwargs = {"format": fmt}
    if exif_orientation is not None:
        exif = img.getexif()
        exif[_EXIF_ORIENTATION_TAG] = exif_orientation
        save_kwargs["exif"] = exif
    img.save(buffer, **save_kwargs)
    return buffer.getvalue()


def _b64_len(data: bytes) -> int:
    return len(base64.b64encode(data))


# ---------------------------------------------------------------------------
# 限制内原样返回 / mime 归一
# ---------------------------------------------------------------------------


def test_within_limits_returns_original_bytes():
    """维度与字节都在预算内：原字节原样返回，不缩放。"""
    data = _make_image_bytes(100, 50)
    result = process_image(data, "image/png")

    assert result.ok is True
    assert result.resized is False
    assert result.data == data
    assert result.mime_type == "image/png"
    assert (result.width, result.height) == (100, 50)
    assert (result.original_width, result.original_height) == (100, 50)
    assert result.hints == []


def test_jpg_mime_normalized_to_jpeg():
    """image/jpg 归一为 image/jpeg。"""
    data = _make_image_bytes(10, 10, fmt="JPEG")
    result = process_image(data, "image/jpg")

    assert result.ok is True
    assert result.mime_type == "image/jpeg"


def test_gif_within_limits_unchanged():
    """gif 属于受支持格式：限制内原样通过。"""
    data = _make_image_bytes(10, 10, fmt="GIF")
    result = process_image(data, "image/gif")

    assert result.ok is True
    assert result.resized is False
    assert result.data == data
    assert result.mime_type == "image/gif"


# ---------------------------------------------------------------------------
# EXIF 方向校正
# ---------------------------------------------------------------------------


def test_exif_orientation_applied_to_dimensions():
    """EXIF orientation=6（顺时针 90°）：报告宽高为校正后的 100x200。"""
    data = _make_image_bytes(200, 100, fmt="JPEG", exif_orientation=6)
    result = process_image(data, "image/jpeg")

    assert result.ok is True
    # 限制内返回原字节，但宽高取 EXIF 校正后的值（）
    assert result.resized is False
    assert (result.width, result.height) == (100, 200)
    assert (result.original_width, result.original_height) == (100, 200)


def test_exif_orientation_applied_through_resize():
    """EXIF 方向参与缩放管线：3000x1000 + orientation=6 校正为 1000x3000 后再 clamp。"""
    data = _make_image_bytes(3000, 1000, fmt="JPEG", exif_orientation=6)
    result = process_image(data, "image/jpeg")

    assert result.ok is True
    assert result.resized is True
    # 校正后 1000x3000：高超限 → 667x2000
    assert (result.width, result.height) == (667, 2000)
    assert (result.original_width, result.original_height) == (1000, 3000)
    # 输出可解码且尺寸与报告一致
    with Image.open(io.BytesIO(result.data)) as out:
        assert out.size == (667, 2000)


# ---------------------------------------------------------------------------
# 格式归一（bmp 等 → PNG）
# ---------------------------------------------------------------------------


def test_bmp_normalized_to_png_with_hint():
    """bmp 转 PNG 并附转换提示。"""
    data = _make_image_bytes(20, 20, fmt="BMP")
    result = process_image(data, "image/bmp")

    assert result.ok is True
    assert result.mime_type == "image/png"
    assert result.hints == ["[Image converted from image/bmp to image/png.]"]
    with Image.open(io.BytesIO(result.data)) as out:
        assert out.format == "PNG"


def test_bmp_resize_hint_uses_final_mime():
    """bmp 经预算链落到 JPEG 时，转换提示的目标用最终输出 mime（）。"""
    data = _make_image_bytes(200, 200, fmt="BMP", noise=True)
    # max_bytes 卡到 PNG（~30KB）超预算、JPEG q80 可进
    result = process_image(data, "image/bmp", max_dimension=100, max_bytes=8000)

    assert result.ok is True
    assert result.resized is True
    assert result.mime_type == "image/jpeg"
    assert result.hints[0] == "[Image converted from image/bmp to image/jpeg.]"


def test_resize_disabled_returns_normalized_bytes():
    """auto_resize 关闭：只做格式归一，不缩放。"""
    data = _make_image_bytes(3000, 100, fmt="BMP")
    result = process_image(data, "image/bmp", resize=False)

    assert result.ok is True
    assert result.resized is False
    assert result.mime_type == "image/png"
    assert result.hints == ["[Image converted from image/bmp to image/png.]"]
    assert (result.width, result.height) == (3000, 100)


# ---------------------------------------------------------------------------
# 字节预算压缩链（PNG 优先 → JPEG 质量递减
# → 尺寸 0.75 倍递减）
# ---------------------------------------------------------------------------


def test_png_preferred_when_within_budget():
    """维度超限但 PNG 编码进预算：优先 PNG。"""
    data = _make_image_bytes(3000, 2000)  # 纯色 PNG 极小
    result = process_image(data, "image/png")

    assert result.ok is True
    assert result.resized is True
    assert result.mime_type == "image/png"
    assert (result.width, result.height) == (2000, 1333)
    assert result.hints == [
        "[Image: original 3000x2000, displayed at 2000x1333. "
        "Multiply coordinates by 1.50 to map to original image.]"
    ]


def test_jpeg_fallback_when_png_over_budget():
    """噪声大图：PNG 超预算 → 落到 JPEG。"""
    data = _make_image_bytes(2100, 2100, noise=True)
    result = process_image(data, "image/png")

    assert result.ok is True
    assert result.resized is True
    assert result.mime_type == "image/jpeg"
    assert (result.width, result.height) == (2000, 2000)
    assert _b64_len(result.data) < MAX_IMAGE_BASE64_BYTES
    assert any("Multiply coordinates by" in hint for hint in result.hints)


def test_budget_chain_descends_until_fit():
    """预算极紧时逐级递减（质量 80/85/70/55/40 → 尺寸 0.75 倍）直到进预算。"""
    data = _make_image_bytes(200, 200, noise=True)
    # 100x100 噪声 PNG ~30KB、JPEG q80 ~3KB：2000 字节预算必走递减链
    result = process_image(data, "image/png", max_dimension=100, max_bytes=2000)

    assert result.ok is True
    assert result.resized is True
    assert result.mime_type == "image/jpeg"
    assert _b64_len(result.data) < 2000
    assert result.width <= 100 and result.height <= 100


def test_over_budget_returns_failure_message():
    """连 1x1 都压不进预算：ok=False + 提示。"""
    data = _make_image_bytes(200, 200, noise=True)
    result = process_image(data, "image/png", max_dimension=100, max_bytes=10)

    assert result.ok is False
    assert (
        result.message
        == "[Image omitted: could not be resized below the inline image size limit.]"
    )


# ---------------------------------------------------------------------------
# 失败语义（提示文案，不抛异常）
# ---------------------------------------------------------------------------


def test_corrupt_bytes_returns_failure_not_raise():
    """受支持格式但字节损坏：解码失败 → ok=False + resized 提示（pi null 路径）。"""
    result = process_image(b"not a real png", "image/png")

    assert result.ok is False
    assert (
        result.message
        == "[Image omitted: could not be resized below the inline image size limit.]"
    )


def test_corrupt_bytes_resize_disabled_passes_through():
    """resize 关闭时不解码（）：坏字节也原样返回 ok=True。"""
    payload = b"not a real png"
    result = process_image(payload, "image/png", resize=False)

    assert result.ok is True
    assert result.data == payload
    assert (result.width, result.height) == (0, 0)  # 量不到尺寸不算失败


def test_undecodable_unsupported_format_returns_convert_failure():
    """不支持格式且无法解码：转换失败 → ok=False + converted 提示（）。"""
    result = process_image(b"not a real bmp", "image/bmp")

    assert result.ok is False
    assert (
        result.message
        == "[Image omitted: could not be converted to a supported inline image format.]"
    )
