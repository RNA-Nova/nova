"""流式 JSON 片段解析测试。"""

import json

from nova_ai.utils import StreamingJsonParser, parse_streaming_json


class TestParseStreamingJson:
    def test_complete_object(self):
        assert parse_streaming_json('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}

    def test_empty_and_none(self):
        assert parse_streaming_json(None) == {}
        assert parse_streaming_json("") == {}
        assert parse_streaming_json("   ") == {}

    def test_truncated_mid_string(self):
        # 工具参数流式到达时的典型截断：json_repair 补全字符串与括号
        assert parse_streaming_json('{"query": "hello wor') == {"query": "hello wor"}

    def test_truncated_mid_structure(self):
        assert parse_streaming_json('{"a": {"b": [1, 2') == {"a": {"b": [1, 2]}}

    def test_invalid_returns_empty_object(self):
        assert parse_streaming_json("not json at all {") == {}

    def test_array_top_level(self):
        result = parse_streaming_json("[1, 2, 3]")
        assert result == [1, 2, 3]

    def test_progressive_parsing(self):
        """模拟流式累积：同一前缀的多个截断点都应可解析"""
        full = '{"name": "read", "arguments": {"path": "/tmp/a.py", "offset": 10}}'
        for i in range(1, len(full) + 1):
            result = parse_streaming_json(full[:i])
            assert isinstance(result, (dict, list))


class TestStreamingJsonParser:
    def _feed_all(self, full: str, delta: int = 7) -> StreamingJsonParser:
        parser = StreamingJsonParser()
        for i in range(0, len(full), delta):
            parser.feed(full[i : i + delta])
        return parser

    def test_initial_value_is_empty_object(self):
        parser = StreamingJsonParser()
        assert parser.value == {}

    def test_truncated_string_visible_per_delta(self):
        """截断字符串逐 delta 可见：闭合补全走 C 级 json.loads"""
        parser = StreamingJsonParser()
        parser.feed('{"query": "hello wor')
        assert parser.value == {"query": "hello wor"}
        parser.feed('ld"}')
        assert parser.value == {"query": "hello world"}

    def test_truncated_mid_string_across_deltas(self):
        """大字符串流式累积：每次 feed 后值都包含已到内容（对齐旧行为）"""
        content = "x = 1\n" * 300
        full = json.dumps({"content": content})
        parser = StreamingJsonParser()
        for i in range(0, len(full), 23):
            parser.feed(full[i : i + 23])
            value = parser.value
            assert isinstance(value, dict)
            # 快照内容必须是真实内容的前缀；唯一例外是转义符被 delta
            # 边界切开时，悬空 \ 会先补成字面反斜杠（json_repair 同款
            # 瞬态伪影，下一 delta 即自愈）
            seen = value.get("content", "")
            if seen.endswith("\\"):
                seen = seen[:-1]
            assert content.startswith(seen)

    def test_truncated_structure(self):
        parser = StreamingJsonParser()
        parser.feed('{"a": {"b": [1, 2')
        assert parser.value == {"a": {"b": [1, 2]}}
        parser.feed("]}}")
        assert parser.value == {"a": {"b": [1, 2]}}

    def test_dangling_token_keeps_previous_snapshot(self):
        """悬空 token（如 ``{"a": ``）不崩溃，保留上一快照"""
        parser = StreamingJsonParser()
        parser.feed('{"a"')
        assert parser.value == {}
        parser.feed(": ")
        assert parser.value == {}
        parser.feed("1")
        assert parser.value == {"a": 1}

    def test_escape_split_across_deltas(self):
        """反斜杠转义被 delta 边界切开时仍正确闭合"""
        parser = StreamingJsonParser()
        parser.feed('{"path": "a\\\\')
        assert parser.value == {"path": "a\\"}
        parser.feed('b"}')
        assert parser.value == {"path": "a\\b"}

    def test_escape_at_delta_boundary(self):
        """转义符恰好在 delta 末尾（悬空反斜杠补全）"""
        parser = StreamingJsonParser()
        parser.feed('{"a": "x\\')
        assert parser.value == {"a": "x\\"}
        parser.feed('n"}')
        assert parser.value == {"a": "x\n"}

    def test_complete_json_fast_path(self):
        """完整 JSON 直接精确命中"""
        full = '{"a": 1, "b": [1, 2, 3]}'
        parser = self._feed_all(full)
        assert parser.value == {"a": 1, "b": [1, 2, 3]}

    def test_invalid_text_stays_empty(self):
        parser = StreamingJsonParser()
        parser.feed("not json at all")
        assert parser.value == {}

    def test_finish_matches_parse_streaming_json(self):
        """终值语义与 parse_streaming_json 完全一致"""
        cases = [
            '{"query": "hello wor',
            '{"a": {"b": [1, 2',
            json.dumps({"content": "x = 1\n" * 100}),
            "not json at all {",
            "",
            "[1, 2, 3]",
        ]
        for text in cases:
            parser = self._feed_all(text)
            assert parser.finish() == parse_streaming_json(text), text

    def test_finish_after_dangling_key_completes_via_repair(self):
        """finish 走 json_repair 兜底：悬空 token 也能得到修复终值"""
        parser = StreamingJsonParser()
        parser.feed('{"a": ')
        assert parser.finish() == parse_streaming_json('{"a": ')

    def test_large_args_perf_smoke(self):
        """性能冒烟：24KB × 600 delta 的流式累积不得退化为 O(n²)
        （旧实现逐 delta 全量 json_repair 实测 ~50s 量级）"""
        import time

        args = {"content": "x = 1\n" * 3000}
        full = json.dumps(args)
        parser = StreamingJsonParser()
        t0 = time.perf_counter()
        for i in range(0, len(full), 40):
            parser.feed(full[i : i + 40])
        assert parser.finish() == args
        elapsed = time.perf_counter() - t0
        assert elapsed < 5.0, f"streaming parse took {elapsed:.1f}s"


class _FuzzOracle:
    """差分 fuzz 的判定器。

    不变式：parser 在任意时刻的 value 只可能是三种之一——
    (a) 累积文本的精确解析；(b) 截断前缀闭合后的精确解析；
    (c) 之前的某个快照（fail-stale）。三者都满足"与真值前缀一致"，
    即 _consistent 的定义。最坏退化是"滞后一个 delta"，不是"错值"。
    """

    @staticmethod
    def consistent(partial, full):
        # stale {} 不可能是 list 前缀的闭合结果，只可能是快照滞留
        if partial == {} and isinstance(full, list):
            return True
        if isinstance(partial, dict) and isinstance(full, dict):
            pk, fk = list(partial.keys()), list(full.keys())
            if pk != fk[: len(pk)]:
                return False
            return all(_FuzzOracle.consistent(partial[k], full[k]) for k in pk)
        if isinstance(partial, list) and isinstance(full, list):
            return len(partial) <= len(full) and all(
                _FuzzOracle.consistent(p, f) for p, f in zip(partial, full)
            )
        if isinstance(partial, str) and isinstance(full, str):
            if full.startswith(partial):
                return True
            # 未完成单元的瞬态伪影（新旧实现同款，已实测对齐），可叠加：
            # - 悬空 \ 被补成字面反斜杠
            # - \uXXXX 代理对的高半区先解码为孤立代理项
            # 复合剥离后再做前缀判定（全部为伪影时剩空串，空串是任何串的前缀）
            while partial:
                if partial.endswith("\\"):
                    partial = partial[:-1]
                elif "\ud800" <= partial[-1] <= "\udbff":
                    partial = partial[:-1]
                else:
                    break
            return full.startswith(partial)
        if isinstance(partial, bool) or isinstance(full, bool):
            return partial is full
        if isinstance(partial, (int, float)) and isinstance(full, (int, float)):
            # 数字字面量中途闭合会得到截断值（1 → 15 的前缀），旧实现同款
            sp, sf = str(partial), str(full)
            return partial == full or (len(sp) < len(sf) and sf.startswith(sp))
        # 其余标量（None 等）：相等即一致
        return partial == full


class TestStreamingJsonParserDifferentialFuzz:
    """种子化差分 fuzz：随机 JSON 文档 × 随机切分点，逐 delta 断言不变式，
    终值断言与真值及旧实现（parse_streaming_json oracle）双一致。"""

    def _gen_string(self, rng):
        alphabet = [
            "a", "z", "0", "9", " ", "\n", "\t", '"', "\\", "/", "é", "中",
            "😀", "\x00", "\x1f",
        ]
        return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12)))

    def _gen_value(self, rng, depth):
        if depth <= 0:
            roll = rng.random()
        else:
            roll = rng.random() * 0.7  # 深层偏标量
        if roll < 0.3:
            return self._gen_string(rng)
        if roll < 0.45:
            return rng.randint(-10**6, 10**6)
        if roll < 0.55:
            return rng.choice([0, 1, -1, 15, 100])  # 触发数字前缀截断
        if roll < 0.62:
            return rng.choice([0.5, 1.25, -3.75])
        if roll < 0.70:
            return rng.choice([True, False, None])
        if roll < 0.85:
            return [self._gen_value(rng, depth - 1) for _ in range(rng.randint(0, 4))]
        return {
            f"k{i}": self._gen_value(rng, depth - 1)
            for i in range(rng.randint(0, 4))
        }

    def _gen_doc(self, rng):
        if rng.random() < 0.8:
            return {
                f"key{i}": self._gen_value(rng, 3) for i in range(rng.randint(1, 5))
            }
        return [self._gen_value(rng, 3) for _ in range(rng.randint(1, 5))]

    def _split(self, rng, text):
        pieces, i = [], 0
        while i < len(text):
            step = rng.randint(1, 17)
            pieces.append(text[i : i + step])
            i += step
        return pieces

    def test_fuzz_per_delta_invariants_and_final_value(self):
        import random

        rng = random.Random(20260901)
        for case in range(400):
            doc = self._gen_doc(rng)
            for ensure_ascii in (True, False):
                text = json.dumps(doc, ensure_ascii=ensure_ascii)
                parser = StreamingJsonParser()
                for piece in self._split(rng, text):
                    parser.feed(piece)
                    value = parser.value
                    assert isinstance(value, (dict, list)), (case, text)
                    assert _FuzzOracle.consistent(value, doc), (
                        case, ensure_ascii, piece, value,
                    )
                assert parser.finish() == doc, (case, text)

    def test_fuzz_truncated_finish_matches_old_implementation(self):
        """截断终值差分：任意截断点上 finish() 必须与旧实现一致"""
        import random

        rng = random.Random(20260902)
        for case in range(300):
            doc = self._gen_doc(rng)
            text = json.dumps(doc)
            parser = StreamingJsonParser()
            for piece in self._split(rng, text):
                parser.feed(piece)
            cut = rng.randint(0, len(text))
            resumed = StreamingJsonParser()
            for piece in self._split(rng, text[:cut]):
                resumed.feed(piece)
            assert resumed.finish() == parse_streaming_json(text[:cut]), (case, cut)
