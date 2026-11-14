"""redact_secrets（sanitize.py）测试——词表逐条 + 误报对抗。"""

from nova_ai.utils.sanitize import REDACTED_SECRET, redact_secrets


class TestBearerPattern:
    def test_bearer_token_masked(self):
        text = 'curl -H "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9" https://api.x.com'
        assert "eyJhbGciOiJIUzI1NiJ9" not in redact_secrets(text)
        assert REDACTED_SECRET in redact_secrets(text)

    def test_bearer_of_good_news_not_masked(self):
        """自然语言不误杀（codex 同款对抗用例）。"""
        text = "I am the Bearer of good news today"
        assert redact_secrets(text) == text

    def test_short_bearer_not_masked(self):
        """过短的非 token 形态不杀（长度约束）。"""
        text = "Bearer abc"
        assert redact_secrets(text) == text


class TestSkPattern:
    def test_openai_key_masked(self):
        text = "OPENAI_API_KEY=sk-proj-abc123def456ghi789"
        assert "sk-proj-abc123def456ghi789" not in redact_secrets(text)

    def test_anthropic_key_masked(self):
        text = "key is sk-ant-api03-xxxxxxxxxxxxxxxxxxxx"
        assert "sk-ant-api03-xxxxxxxxxxxxxxxxxxxx" not in redact_secrets(text)

    def test_normal_word_not_masked(self):
        """sk 开头的普通词不杀（长度约束）。"""
        text = "use sk-8 for this"
        assert redact_secrets(text) == text


class TestAkiaPattern:
    def test_aws_key_masked(self):
        text = "aws_access_key_id = AKIAIOSFODNN7EXAMPLE"
        assert "AKIAIOSFODNN7EXAMPLE" not in redact_secrets(text)

    def test_akia_like_not_masked(self):
        text = "AKIA123 too short"
        assert redact_secrets(text) == text


class TestApiKeyAssignmentPattern:
    def test_assignment_forms_masked(self):
        for text in (
            "api_key=abcd1234efgh5678",
            "apikey: abcd1234efgh5678",
            'api-key = "abcd1234efgh5678"',
            "API_KEY=abcd1234efgh5678",
        ):
            assert "abcd1234efgh5678" not in redact_secrets(text), text


class TestPlainTextUntouched:
    def test_normal_text_unchanged(self):
        text = "run pytest tests -q && git status --porcelain"
        assert redact_secrets(text) == text


class TestJwtPattern:
    """JWT 形态（OAuth access/id token 主力载体）。"""

    def test_jwt_masked(self):
        token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c"
        text = f"login failed, got token {token} in response"
        assert token not in redact_secrets(text)
        assert REDACTED_SECRET in redact_secrets(text)

    def test_eyj_short_not_masked(self):
        """过短/不成立三段形态不杀（长度约束）。"""
        text = "eyJabc def"
        assert redact_secrets(text) == text

    def test_natural_text_not_masked(self):
        text = "this eyJ is not a token at all"
        assert redact_secrets(text) == text


class TestTokenAssignmentPattern:
    """token 族键名赋值/JSON 形态（OAuth 响应体的落盘形状）。"""

    def test_oauth_keys_masked(self):
        for text in (
            "access_token=abcdefgh12345678",
            '"refresh_token": "abcdefgh12345678"',
            "id_token = 'abcdefgh12345678'",
            'authorization_code="abcdefgh12345678"',
            "code_verifier: abcdefgh12345678",
        ):
            assert "abcdefgh12345678" not in redact_secrets(text), text

    def test_non_secret_key_not_masked(self):
        """键名单外的近似词不杀（词边界 + 赋值形约束）。"""
        for text in (
            "access_tokenize=true and tokenizer: abcdefgh12345678",
            "session_token_count=123456",  # token 族不含 session_token_count
        ):
            assert redact_secrets(text) == text, text

    def test_short_value_not_masked(self):
        text = "access_token=abc"
        assert redact_secrets(text) == text


def test_redacts_github_token_family():
    """GitHub token 族（ghp_/gho_/ghu_/ghs_/ghr_/github_pat_）——Copilot
    凭据源走 GH_TOKEN/GITHUB_TOKEN，代理 401 回显请求头是常见泄漏面。"""

    text = "bad credentials: ghp_abcdefghijklmnopqrstuvwxyz0123"
    assert "ghp_" not in redact_secrets(text)
    text2 = "Authorization: token github_pat_11ABCDEFG0_abcdefghijklmnopqrstuv"
    assert "github_pat_11ABCDEFG0" not in redact_secrets(text2)


def test_redacts_client_secret_assignment():
    """键名赋值形扩 client_secret/secret_key（OAuth client 配置族）。"""

    assert "supersecretvalue" not in redact_secrets('client_secret: "supersecretvalue"')
    assert "supersecretvalue" not in redact_secrets("secret_key=supersecretvalue")
