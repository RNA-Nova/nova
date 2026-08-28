"""OpenAI Codex OAuth flow 单元测试。"""

import pytest

from nova_ai.auth.oauth.openai_codex import (
    _create_authorization_flow,
    _parse_authorization_input,
)


@pytest.mark.asyncio
async def test_create_authorization_flow_url_encodes_query():
    flow = await _create_authorization_flow()
    url = flow["url"]
    # scope 包含空格，必须被编码成 %20
    assert "scope=openid%20profile%20email%20offline_access" in url
    assert "response_type=code" in url
    assert "code_challenge=" in url
    assert "state=" in url


def test_parse_authorization_input_from_url():
    parsed = _parse_authorization_input(
        "http://localhost:1455/auth/callback?code=abc&state=xyz"
    )
    assert parsed == {"code": "abc", "state": "xyz"}


def test_parse_authorization_input_from_hash():
    parsed = _parse_authorization_input("abc#xyz")
    assert parsed == {"code": "abc", "state": "xyz"}


def test_parse_authorization_input_from_query_string():
    parsed = _parse_authorization_input("code=abc&state=xyz")
    assert parsed == {"code": "abc", "state": "xyz"}


def test_parse_authorization_input_plain_code():
    parsed = _parse_authorization_input("abc")
    assert parsed == {"code": "abc"}
