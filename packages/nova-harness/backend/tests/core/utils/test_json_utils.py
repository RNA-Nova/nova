"""strip_json_comments 测试。"""

from nova_harness.core.utils.json import strip_json_comments


def test_line_comment_removed():
    content = '{\n  // comment\n  "a": 1\n}'
    assert '"a": 1' in strip_json_comments(content)
    assert "comment" not in strip_json_comments(content)


def test_block_comment_removed_preserving_newlines():
    content = '{\n  /* multi\n     line */\n  "a": 1\n}'
    stripped = strip_json_comments(content)
    assert "comment" not in stripped and "multi" not in stripped
    assert stripped.count("\n") == content.count("\n")


def test_comment_markers_inside_strings_preserved():
    content = '{"url": "https://example.com/* not comment */", "b": "//x"}'
    stripped = strip_json_comments(content)
    assert "https://example.com/* not comment */" in stripped
    assert '"//x"' in stripped


def test_escaped_quote_in_string():
    content = '{"a": "he said \\"//hi\\"" } // trailing'
    stripped = strip_json_comments(content)
    assert "trailing" not in stripped
    assert "//hi" in stripped


def test_models_json_with_comments_loads(tmp_path):
    import json

    from nova_harness.core.model import ModelRuntime
    from tests._helpers.auth_storage import auth_storage_in_memory

    models_path = tmp_path / "models.json"
    models_path.write_text(
        """{
        // 自定义 provider
        "providers": {
            "custom": {
                "base_url": "http://x/v1", /* 内网代理 */
                "api": "openai-completions",
                "models": [{"id": "m1"}]
            }
        }
    }""",
        encoding="utf-8",
    )
    runtime = ModelRuntime(auth_storage_in_memory({}), str(models_path))
    assert runtime.find("custom", "m1") is not None
    assert runtime.get_error() is None


def test_invalid_json_still_reports_error(tmp_path):
    from nova_harness.core.model import ModelRuntime
    from tests._helpers.auth_storage import auth_storage_in_memory

    models_path = tmp_path / "models.json"
    models_path.write_text("{invalid", encoding="utf-8")
    runtime = ModelRuntime(auth_storage_in_memory({}), str(models_path))
    assert runtime.get_error() is not None
