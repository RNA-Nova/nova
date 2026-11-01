"""Unicode 代理项清理测试（对齐 TS sanitize-unicode.ts）。"""

from nova_ai.utils import sanitize_surrogates


class TestSanitizeSurrogates:
    def test_valid_emoji_preserved(self):
        assert sanitize_surrogates("Hello 🙈 World") == "Hello 🙈 World"

    def test_unpaired_high_surrogate_removed(self):
        unpaired = chr(0xD83D)  # 高代理项，无低代理项
        assert sanitize_surrogates(f"Text {unpaired} here") == "Text  here"

    def test_unpaired_low_surrogate_removed(self):
        unpaired = chr(0xDC00)  # 低代理项，无高代理项
        assert sanitize_surrogates(f"Text {unpaired} here") == "Text  here"

    def test_multiple_unpaired_removed(self):
        text = f"a{chr(0xD800)}b{chr(0xDFFF)}c"
        assert sanitize_surrogates(text) == "abc"

    def test_valid_pair_at_boundary_preserved(self):
        # 高低代理项相邻构成有效对，不受影响
        pair = chr(0xD83D) + chr(0xDE00)
        assert sanitize_surrogates(f"x{pair}y") == f"x{pair}y"

    def test_empty_and_none(self):
        assert sanitize_surrogates("") == ""

    def test_plain_ascii_unchanged(self):
        assert sanitize_surrogates("plain text 123") == "plain text 123"

    def test_cjk_preserved(self):
        assert sanitize_surrogates("中文测试") == "中文测试"
