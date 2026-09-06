"""流式编码容错解码（StreamDecoder）测试——Windows OEM 回退场景。"""

from nova_coding_agent.tools_common.output_decode import StreamDecoder


class TestUtf8Primary:
    def test_plain_utf8_passthrough(self):
        dec = StreamDecoder(fallback="gbk")
        assert dec.decode("你好 bash\n".encode("utf-8")) == "你好 bash\n"

    def test_split_multibyte_char_across_chunks(self):
        """跨块的半个 UTF-8 序列由增量状态承接（strict 不误判回退）。"""
        dec = StreamDecoder(fallback="gbk")
        raw = "版本".encode("utf-8")
        out = dec.decode(raw[:3]) + dec.decode(raw[3:]) + dec.flush()
        assert out == "版本"

    def test_flush_pending_partial(self):
        dec = StreamDecoder(fallback="gbk")
        raw = "好".encode("utf-8")
        assert dec.decode(raw[:1]) == ""  # 待定
        assert dec.decode(raw[1:]) + dec.flush() == "好"


class TestOemFallback:
    def test_gbk_chunk_falls_back(self):
        """整块 GBK（cmd/query 等原生程序输出）回退 OEM 解码。"""
        dec = StreamDecoder(fallback="gbk")
        # "版本" 的 GBK 字节在 UTF-8 下非法 → 整块回退
        assert dec.decode("版本".encode("gbk")) == "版本"

    def test_gbk_console_banner(self):
        """cmd 横幅形态（实机采样）：回退后中文完整。"""
        dec = StreamDecoder(fallback="gbk")
        raw = "Microsoft Windows [版本 10.0.26200]".encode("gbk")
        assert dec.decode(raw) == "Microsoft Windows [版本 10.0.26200]"

    def test_mixed_stream(self):
        """bash 自身（UTF-8）与原生程序（GBK）交替成块。"""
        dec = StreamDecoder(fallback="gbk")
        out = (
            dec.decode("$ query session\n".encode("utf-8"))
            + dec.decode("会话名  用户名\n".encode("gbk"))
            + dec.decode("done\n".encode("utf-8"))
            + dec.flush()
        )
        assert out == "$ query session\n会话名  用户名\ndone\n"

    def test_gbk_char_split_across_chunks(self):
        """半个 GBK 字符跨块：OEM 侧增量状态承接。"""
        dec = StreamDecoder(fallback="gbk")
        raw = "版本".encode("gbk")
        out = dec.decode(raw[:1]) + dec.decode(raw[1:]) + dec.flush()
        assert out == "版本"


class TestNoFallbackLegacyBehavior:
    def test_invalid_bytes_become_replacement(self):
        """无回退 = 旧行为：非法字节落替换符，不抛不丢。"""
        dec = StreamDecoder()
        out = dec.decode("版本".encode("gbk")) + dec.flush()
        assert "版本" not in out  # GBK 字节在 UTF-8+replace 下不可逆
        assert out  # 有输出（替换符），不崩

    def test_utf8_unchanged(self):
        dec = StreamDecoder()
        assert dec.decode("你好".encode("utf-8")) == "你好"
