"""read 工具测试：文本读取、图片管线（对齐 pi processImage）、截断提示、MIME 魔数嗅探。"""

import asyncio
import os
import tempfile

import pytest


def _load_executor():
    """从源码目录加载 read 工具的 executor 模块。"""
    import importlib.util

    executor_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "tools", "read.py"
    )
    spec = importlib.util.spec_from_file_location("_test_tool_read", executor_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from nova_harness.types.resources.tools import (
        NULL_TOOL_SETTINGS,
        ToolContext,
    )

    context = ToolContext(cwd=os.getcwd(), settings=NULL_TOOL_SETTINGS)
    return module.Tool(context)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def tmpdir():
    # realpath：fd/rg 会规范化搜索根（macOS /var 软链），相对化前缀才干净
    with tempfile.TemporaryDirectory() as d:
        yield os.path.realpath(d)


def test_read_text_file(tmpdir):
    path = os.path.join(tmpdir, "sample.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("line1\nline2\nline3\n")

    executor = _load_executor()
    result = _run(executor.execute("id", {"path": path}))

    assert len(result.content) == 1
    assert "line1" in result.content[0].text
    assert result.details["lines"] == 3


def test_read_offset_limit(tmpdir):
    path = os.path.join(tmpdir, "sample.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("a\nb\nc\nd\n")

    executor = _load_executor()
    result = _run(executor.execute("id", {"path": path, "offset": 2, "limit": 2}))

    # 提取 ``` 代码块内容，避免路径中包含字母造成误判
    text = result.content[0].text
    parts = text.split("```")
    code = parts[1].split("\n", 1)[-1].strip("\n`")
    lines = [ln for ln in code.splitlines() if ln]
    assert lines == ["b", "c"]


def test_tool_metadata_valid():
    """Tool 类必须声明完整元数据（类属性）。"""
    executor = _load_executor()
    assert executor.name == "read"
    assert isinstance(executor.description, str) and executor.description
    assert isinstance(executor.parameters, dict)
    assert executor.parameters.get("type") == "object"


# 1x1 红色 PNG（PIL 现造：旧硬编码 hex 样本字节错位，严格解码会拒绝——
# 对齐 pi 的 processImage 真实解码后必须用合法样本）
def _write_tiny_png(path):
    from PIL import Image

    Image.new("RGBA", (1, 1), (255, 0, 0, 255)).save(path, format="PNG")


def _make_exec_context(model_input_types):
    """构造执行期 ToolExecContext（模型经 execute 第 5 参注入）。"""
    from types import SimpleNamespace

    from nova_harness.types.resources.tools import ToolExecContext

    return ToolExecContext(model=SimpleNamespace(input_types=model_input_types))


def test_read_image_non_vision_model_omits_image(tmpdir):
    """非视觉模型：返回提示且不附带 ImageContent（对齐 pi getNonVisionImageNote）。"""
    path = os.path.join(tmpdir, "tiny.png")
    _write_tiny_png(path)

    executor = _load_executor()
    result = _run(
        executor.execute("id", {"path": path}, ctx=_make_exec_context(["text"]))
    )

    from nova_ai import ImageContent

    assert "does not support images" in result.content[0].text
    assert not any(isinstance(c, ImageContent) for c in result.content)
    assert result.details.get("omitted") == "non_vision_model"


def test_read_image_vision_model_returns_image(tmpdir):
    """视觉模型：正常返回 ImageContent。"""
    path = os.path.join(tmpdir, "tiny.png")
    _write_tiny_png(path)

    executor = _load_executor()
    result = _run(
        executor.execute(
            "id", {"path": path}, ctx=_make_exec_context(["text", "image"])
        )
    )

    from nova_ai import ImageContent

    assert any(isinstance(c, ImageContent) for c in result.content)


# ---------------------------------------------------------------------------
# read 图片管线（对齐 pi processImage：to_thread 异步化、失败语义、hints）
# ---------------------------------------------------------------------------


def _make_noise_png(path, width=2100, height=2100):
    """生成确定性随机噪声 PNG（PNG 压缩不动，必超字节预算落到 JPEG）。"""
    import random

    from PIL import Image

    rng = random.Random(42)
    img = Image.frombytes("RGB", (width, height), rng.randbytes(width * height * 3))
    img.save(path, format="PNG")


def test_read_image_does_not_block_event_loop(tmpdir):
    """process_image 经 asyncio.to_thread 移出事件循环（对齐 operations 并发约定）。

    同步调用会让 ticker 一次都跑不到（ticks == 0）。
    """
    path = os.path.join(tmpdir, "big.png")
    _make_noise_png(path)

    executor = _load_executor()

    async def scenario():
        ticks = 0
        stop = False

        async def ticker():
            nonlocal ticks
            while not stop:
                ticks += 1
                await asyncio.sleep(0.002)

        ticker_task = asyncio.create_task(ticker())
        result = await executor.execute("id", {"path": path})
        stop = True
        await ticker_task
        return ticks, result

    ticks, result = _run(scenario())
    assert ticks > 0
    assert not result.is_error


def test_read_image_process_failure_not_error(tmpdir):
    """图片无法解码：给提示文本让模型继续，不标 is_error（对齐 pi read.ts）。"""
    from nova_ai import ImageContent

    path = os.path.join(tmpdir, "broken.png")
    with open(path, "wb") as f:
        f.write(b"not a real png")

    executor = _load_executor()
    result = _run(executor.execute("id", {"path": path}))

    assert result.is_error is False
    assert (
        "could not be resized below the inline image size limit"
        in result.content[0].text
    )
    assert not any(isinstance(c, ImageContent) for c in result.content)
    assert result.details.get("omitted") == "image_processing_failed"


def test_read_image_resize_dimension_hint(tmpdir):
    """缩放后输出文本带坐标映射系数提示（对齐 pi formatDimensionNote）。"""
    from PIL import Image

    path = os.path.join(tmpdir, "wide.png")
    Image.new("RGB", (3000, 2000), (200, 30, 30)).save(path, format="PNG")

    executor = _load_executor()
    result = _run(executor.execute("id", {"path": path}))

    assert not result.is_error
    text = result.content[0].text
    assert (
        "[Image: original 3000x2000, displayed at 2000x1333. "
        "Multiply coordinates by 1.50 to map to original image.]" in text
    )
    assert result.details["resized"] is True


def test_read_bmp_conversion_hint(tmpdir):
    """bmp 归一为 PNG 并附转换提示（对齐 pi conversionHint 文案）。"""
    from nova_ai import ImageContent
    from PIL import Image

    path = os.path.join(tmpdir, "shot.bmp")
    Image.new("RGB", (20, 20), (30, 200, 30)).save(path, format="BMP")

    executor = _load_executor()
    result = _run(executor.execute("id", {"path": path}))

    assert not result.is_error
    assert "[Image converted from image/bmp to image/png.]" in result.content[0].text
    image_blocks = [c for c in result.content if isinstance(c, ImageContent)]
    assert len(image_blocks) == 1
    assert image_blocks[0].mime_type == "image/png"


def test_read_nonexistent_file_still_is_error(tmpdir):
    """真正的读取失败（文件不存在）仍标 is_error——失败语义只放宽图片处理。"""
    executor = _load_executor()
    result = _run(executor.execute("id", {"path": os.path.join(tmpdir, "nope.png")}))

    assert result.is_error is True


# ---------------------------------------------------------------------------
# prompt 元数据（对齐 pi promptSnippet/Guidelines）
# ---------------------------------------------------------------------------


def test_read_prompt_metadata():
    """read 声明 prompt_snippet 与 prompt_guidelines（对齐 pi promptSnippet/Guidelines）。"""
    executor = _load_executor()
    assert isinstance(executor.prompt_snippet, str) and executor.prompt_snippet
    guidelines = executor.prompt_guidelines
    assert isinstance(guidelines, list) and guidelines
    assert all(isinstance(g, str) and g for g in guidelines)


# ---------------------------------------------------------------------------
# read 截断提示文案（对齐 pi read.ts：首行大小 / 字节限标记）
# ---------------------------------------------------------------------------


def test_read_first_line_exceeds_reports_size(tmpdir):
    """首行即超字节预算：提示带该行实际大小（对齐 pi firstLineExceedsLimit 文案）。"""
    path = os.path.join(tmpdir, "wide.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("x" * (60 * 1024) + "\nshort\n")

    executor = _load_executor()
    result = _run(executor.execute("id", {"path": path}))

    assert not result.is_error
    text = result.content[0].text
    assert "[Line 1 is 60.0KB, exceeds 50.0KB limit." in text
    assert "sed -n '1p'" in text


def test_read_bytes_truncation_notice_marks_limit(tmpdir):
    """字节限截断：continuation 提示带 (50.0KB limit) 标记（对齐 pi bytes 变体）。"""
    path = os.path.join(tmpdir, "fat.txt")
    line = "y" * 1023  # 每行约 1KB（含换行），100 行约 100KB > 50KB
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join([line] * 100) + "\n")

    executor = _load_executor()
    result = _run(executor.execute("id", {"path": path}))

    assert not result.is_error
    assert result.details["truncated"] is True
    assert result.details["truncated_by"] == "bytes"
    text = result.content[0].text
    assert "(50.0KB limit)" in text
    assert "Use offset=" in text


def test_read_lines_truncation_notice_without_bytes_mark(tmpdir):
    """行数限截断：提示不带字节限标记（对齐 pi lines 变体）。"""
    path = os.path.join(tmpdir, "long.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(f"line{i}" for i in range(1, 3001)) + "\n")

    executor = _load_executor()
    result = _run(executor.execute("id", {"path": path}))

    assert not result.is_error
    assert result.details["truncated"] is True
    assert result.details["truncated_by"] == "lines"
    text = result.content[0].text
    assert "Use offset=2001 to continue." in text
    assert "(50.0KB limit)" not in text


# ---------------------------------------------------------------------------
# 图片 MIME 魔数嗅探（对齐 pi detectSupportedImageMimeType）
# ---------------------------------------------------------------------------


def test_detect_image_mime_type_magic():
    """五种格式魔数命中；RIFF 非 WEBP（如 wav）不误判（旧魔数表 startswith RIFF 会误判）。"""
    from nova_coding_agent.tools_common.operations import detect_image_mime_type

    assert detect_image_mime_type(b"\xff\xd8\xff\xe0" + b"\x00" * 8) == "image/jpeg"
    assert detect_image_mime_type(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4) == "image/png"
    assert detect_image_mime_type(b"GIF89a" + b"\x00" * 6) == "image/gif"
    assert detect_image_mime_type(b"RIFF\x00\x00\x00\x00WEBP") == "image/webp"
    assert detect_image_mime_type(b"BM" + b"\x00" * 10) == "image/bmp"
    assert detect_image_mime_type(b"RIFF\x00\x00\x00\x00WAVE") is None
    assert detect_image_mime_type(b"plain text!") is None


def test_read_riff_non_webp_treated_as_text(tmpdir):
    """RIFF 非 WEBP 文件（wav）：不再误判为图片，按文本读取。"""
    path = os.path.join(tmpdir, "sound.wav")
    with open(path, "wb") as f:
        f.write(b"RIFF\x24\x00\x00\x00WAVEfmt hello-text")

    executor = _load_executor()
    result = _run(executor.execute("id", {"path": path}))

    assert not result.is_error
    assert "hello-text" in result.content[0].text


def test_read_image_mime_sniffed_from_magic_bytes(tmpdir):
    """JPEG 字节配 .png 扩展名：MIME 以魔数为准，不按扩展名张冠李戴。"""
    from nova_ai import ImageContent
    from PIL import Image

    path = os.path.join(tmpdir, "mislabeled.png")
    Image.new("RGB", (10, 10), (10, 10, 200)).save(path, format="JPEG")

    executor = _load_executor()
    result = _run(executor.execute("id", {"path": path}))

    assert not result.is_error
    images = [c for c in result.content if isinstance(c, ImageContent)]
    assert len(images) == 1
    assert images[0].mime_type == "image/jpeg"
    assert result.details["mime"] == "image/jpeg"


def test_read_image_extensionless_sniffed(tmpdir):
    """无扩展名图片：魔数嗅探命中，走图片管线且 MIME 正确。"""
    from nova_ai import ImageContent

    path = os.path.join(tmpdir, "noext")
    _write_tiny_png(path)

    executor = _load_executor()
    result = _run(executor.execute("id", {"path": path}))

    assert not result.is_error
    assert any(isinstance(c, ImageContent) for c in result.content)
    assert result.details["mime"] == "image/png"
