"""OpenAI Codex OAuth flow 单元测试。"""

import pytest
from nova_ai.auth.oauth.openai_codex import _create_authorization_flow


@pytest.mark.asyncio
async def test_create_authorization_flow_url_encodes_query():
    flow = await _create_authorization_flow()
    url = flow.url
    # scope 包含空格，必须被编码成 %20
    assert "scope=openid%20profile%20email%20offline_access" in url
    assert "response_type=code" in url
    assert "code_challenge=" in url
    assert "state=" in url
