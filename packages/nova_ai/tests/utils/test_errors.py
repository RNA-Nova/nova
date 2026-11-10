"""normalize_error 六级阶梯与 NormalizedError（utils/errors.py 归一枢纽）。"""

import anthropic
import httpx
import httpx2
import openai
import pytest
from nova_ai.utils.errors import (
    extract_retry_after_ms,
    get_header,
    headers_of,
    normalize_error,
)
from nova_protocol import NovaErrorKind


class _StatusError(Exception):
    """带状态码/响应头的假 provider 错误（SDK 属性形状）。"""

    def __init__(self, message, *, status_code=None, headers=None):
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code
        self.headers = headers


class TestSelfDeclaredKind:
    def test_declared_kind_wins(self):
        class Declared(Exception):
            nova_error_kind = NovaErrorKind.EXECUTOR_UNAVAILABLE

        err = Declared("executor down")
        result = normalize_error(err)
        assert result.kind is NovaErrorKind.EXECUTOR_UNAVAILABLE
        assert result.retryable is False
        assert result.cause is err

    def test_declared_kind_beats_overflow_text(self):
        class Declared(Exception):
            nova_error_kind = NovaErrorKind.SANDBOX_DENIED

        result = normalize_error(Declared("prompt is too long"))
        assert result.kind is NovaErrorKind.SANDBOX_DENIED


class TestOverflowTier:
    def test_overflow_text(self):
        result = normalize_error(Exception("prompt is too long: 213462 tokens"))
        assert result.kind is NovaErrorKind.CONTEXT_OVERFLOW
        assert result.retryable is False

    def test_litellm_pre_call_overflow(self):
        """LiteLLM 预检 400 的 ContextWindowExceededError 归溢出家族
        （camelCase 类名 + "Context Window exceeded" 文本——pi #11494 同病）。"""
        result = normalize_error(
            Exception(
                "Error code: 400 - litellm.ContextWindowExceededError: "
                "litellm.BadRequestError: litellm._pre_call_checks: Context "
                "Window exceeded for given call. Max Input Tokens=262144, "
                "Got=265262"
            )
        )
        assert result.kind is NovaErrorKind.CONTEXT_OVERFLOW
        assert result.retryable is False

    def test_non_overflow_veto_falls_through_to_rate_limit_text(self):
        # "rate limit" 否决溢出 → 后续级归 RATE_LIMITED（可重试）
        result = normalize_error(Exception("rate limit: prompt is too long"))
        assert result.kind is NovaErrorKind.RATE_LIMITED
        assert result.retryable is True


class TestUsageLimitTier:
    @pytest.mark.parametrize(
        "text",
        [
            "insufficient_quota: check your plan",
            "You have hit your usage limit",
            "out of budget",
            "quota exceeded for today",
            "billing hard limit reached",
            "available balance is insufficient",
        ],
    )
    def test_usage_limit_family_not_retryable(self, text):
        result = normalize_error(Exception(text))
        assert result.kind is NovaErrorKind.USAGE_LIMIT_EXCEEDED
        assert result.retryable is False

    def test_usage_limit_beats_429_status(self):
        # 429 的"额度用尽"与"临时限流"分流：限额词表先于状态码
        result = normalize_error(_StatusError("insufficient_quota", status_code=429))
        assert result.kind is NovaErrorKind.USAGE_LIMIT_EXCEEDED
        assert result.retryable is False


class TestSdkClassTier:
    def test_openai_timeout(self):
        req = httpx.Request("POST", "https://api.example.com/v1")
        result = normalize_error(openai.APITimeoutError(request=req))
        assert result.kind is NovaErrorKind.REQUEST_TIMEOUT
        assert result.retryable is True

    def test_openai_connection(self):
        req = httpx.Request("POST", "https://api.example.com/v1")
        result = normalize_error(openai.APIConnectionError(request=req))
        assert result.kind is NovaErrorKind.HTTP_CONNECTION_FAILED
        assert result.retryable is True

    def test_openai_rate_limit(self):
        req = httpx.Request("POST", "https://api.example.com/v1")
        resp = httpx.Response(429, request=req)
        result = normalize_error(
            openai.RateLimitError("slow down", response=resp, body=None)
        )
        assert result.kind is NovaErrorKind.RATE_LIMITED
        assert result.retryable is True

    def test_anthropic_authentication(self):
        req = httpx2.Request("POST", "https://api.anthropic.com/v1")
        resp = httpx2.Response(401, request=req)
        result = normalize_error(
            anthropic.AuthenticationError("bad key", response=resp, body=None)
        )
        assert result.kind is NovaErrorKind.UNAUTHORIZED
        assert result.retryable is False

    def test_anthropic_connection(self):
        req = httpx2.Request("POST", "https://api.anthropic.com/v1")
        result = normalize_error(anthropic.APIConnectionError(request=req))
        assert result.kind is NovaErrorKind.HTTP_CONNECTION_FAILED

    def test_anthropic_overloaded(self):
        req = httpx2.Request("POST", "https://api.anthropic.com/v1")
        resp = httpx2.Response(529, request=req)
        result = normalize_error(
            anthropic.OverloadedError("Overloaded", response=resp, body=None)
        )
        assert result.kind is NovaErrorKind.OVERLOADED
        assert result.retryable is True


class TestStatusTier:
    @pytest.mark.parametrize(
        ("status", "kind", "retryable"),
        [
            (400, NovaErrorKind.BAD_REQUEST, False),
            (401, NovaErrorKind.UNAUTHORIZED, False),
            (403, NovaErrorKind.UNAUTHORIZED, False),
            (404, NovaErrorKind.MODEL_NOT_FOUND, False),
            (408, NovaErrorKind.REQUEST_TIMEOUT, True),
            (409, NovaErrorKind.OVERLOADED, True),
            (429, NovaErrorKind.RATE_LIMITED, True),
            (500, NovaErrorKind.UPSTREAM_ERROR, True),
            (502, NovaErrorKind.UPSTREAM_ERROR, True),
            (529, NovaErrorKind.OVERLOADED, True),
        ],
    )
    def test_status_mapping(self, status, kind, retryable):
        result = normalize_error(_StatusError("boom", status_code=status))
        assert result.kind is kind
        assert result.retryable is retryable
        assert result.http_status == status

    def test_non_mapped_status_falls_through(self):
        # 3xx 无归类 → INTERNAL
        result = normalize_error(_StatusError("redirect?", status_code=302))
        assert result.kind is NovaErrorKind.INTERNAL


class TestTransportTier:
    def test_builtin_timeout(self):
        result = normalize_error(TimeoutError("timed out"))
        assert result.kind is NovaErrorKind.REQUEST_TIMEOUT

    def test_builtin_connection_error(self):
        result = normalize_error(ConnectionError("refused"))
        assert result.kind is NovaErrorKind.HTTP_CONNECTION_FAILED

    def test_cause_chain_httpx(self):
        err = Exception("sdk wrap")
        err.__cause__ = httpx.ConnectError("refused")
        result = normalize_error(err)
        assert result.kind is NovaErrorKind.HTTP_CONNECTION_FAILED

    def test_cause_chain_httpx2_timeout(self):
        err = Exception("sdk wrap")
        err.__cause__ = httpx2.TimeoutException("slow")
        result = normalize_error(err)
        assert result.kind is NovaErrorKind.REQUEST_TIMEOUT


class TestCauseDetail:
    """连接级错误拼 cause 链最深层（对齐 pi 4c992154d withCauseDetail）。"""

    def test_openai_connection_appends_deepest_cause(self):
        """真实 SDK 形状：APIConnectionError 的通用文案 + httpx 最深层真因。"""
        req = httpx.Request("POST", "https://api.example.com/v1")
        err = openai.APIConnectionError(request=req)
        err.__cause__ = httpx.ConnectError("connect ECONNREFUSED 127.0.0.1:8080")
        result = normalize_error(err)
        assert "Connection error" in result.message
        assert "ECONNREFUSED" in result.message

    def test_deepest_of_multi_level_chain_wins(self):
        err = Exception("sdk wrap")
        mid = Exception("mid layer")
        leaf = OSError("name resolution failed")
        err.__cause__ = mid
        mid.__cause__ = leaf
        result = normalize_error(err)
        assert "name resolution failed" in result.message
        assert "mid layer" not in result.message

    def test_cause_code_appended_when_missing(self):
        class _CodedError(Exception):
            code = "EAI_AGAIN"

        err = Exception("Connection error.")
        err.__cause__ = _CodedError("getaddrinfo failed")
        result = normalize_error(err)
        assert "getaddrinfo failed (EAI_AGAIN)" in result.message

    def test_no_cause_leaves_message_unchanged(self):
        result = normalize_error(Exception("plain failure"))
        assert result.message == "plain failure"

    def test_status_present_skips_cause_detail(self):
        """有 status 的错误不拼 cause（响应体已承载真因）。"""
        err = _StatusError("boom", status_code=500)
        err.__cause__ = Exception("should not appear")
        result = normalize_error(err)
        assert "should not appear" not in result.message


class TestRateLimitTextTier:
    def test_rate_limit_text_without_status(self):
        result = normalize_error(Exception("Rate limit reached, retry soon"))
        assert result.kind is NovaErrorKind.RATE_LIMITED
        assert result.retryable is True

    def test_too_many_requests_text(self):
        result = normalize_error(Exception("Too many requests"))
        assert result.kind is NovaErrorKind.RATE_LIMITED

    def test_overloaded_text(self):
        result = normalize_error(Exception("Overloaded"))
        assert result.kind is NovaErrorKind.OVERLOADED
        assert result.retryable is True


class TestInternalFallback:
    def test_plain_error(self):
        result = normalize_error(ValueError("random bug"))
        assert result.kind is NovaErrorKind.INTERNAL
        assert result.retryable is False
        assert "random bug" in result.message

    def test_non_exception_input(self):
        result = normalize_error({"weird": 1})
        assert result.kind is NovaErrorKind.INTERNAL
        assert result.retryable is False
        assert result.cause is None
        assert "weird" in result.message


class TestCauseChain:
    """链走（铁律：包装层不掩盖底层分类——codex #[from] 保源同款）。"""

    def test_declared_kind_in_cause(self):
        class Declared(Exception):
            nova_error_kind = NovaErrorKind.UNAUTHORIZED

        wrapper = Exception("OAuth refresh failed for kimi-coding")
        wrapper.__cause__ = Declared("invalid_grant")
        result = normalize_error(wrapper)
        assert result.kind is NovaErrorKind.UNAUTHORIZED
        assert result.retryable is False

    def test_sdk_class_in_cause(self):
        req = httpx.Request("POST", "https://api.example.com/v1")
        wrapper = Exception("API key auth check failed for provider x")
        wrapper.__cause__ = openai.RateLimitError(
            "slow down", response=httpx.Response(429, request=req), body=None
        )
        result = normalize_error(wrapper)
        assert result.kind is NovaErrorKind.RATE_LIMITED
        assert result.retryable is True

    def test_models_error_wrapper_preserves_cause_kind(self):
        """ModelsError 包装 OAuthFlowError：底层自声明沿 __cause__ 链上浮。"""
        from nova_ai.auth.oauth.errors import OAuthFlowError
        from nova_ai.auth.resolve import ModelsError

        wrapper = ModelsError(
            "oauth",
            "OAuth refresh failed for kimi-coding",
            OAuthFlowError(
                "Kimi token refresh unauthorized: re-login required",
                kind=NovaErrorKind.UNAUTHORIZED,
                status_code=401,
            ),
        )
        result = normalize_error(wrapper)
        assert result.kind is NovaErrorKind.UNAUTHORIZED
        assert result.http_status == 401

    def test_models_error_kind_param_self_declares(self):
        from nova_ai.auth.resolve import ModelsError

        err = ModelsError(
            "oauth", "token expires too soon", kind=NovaErrorKind.UNAUTHORIZED
        )
        assert err.nova_error_kind is NovaErrorKind.UNAUTHORIZED
        assert normalize_error(err).kind is NovaErrorKind.UNAUTHORIZED

    def test_outer_declared_wins_over_inner(self):
        """外层先赢：外层自声明优先于内层任何分类。"""

        class Inner(Exception):
            nova_error_kind = NovaErrorKind.RATE_LIMITED

        class Outer(Exception):
            nova_error_kind = NovaErrorKind.INTERNAL

        outer = Outer("outer")
        outer.__cause__ = Inner("inner")
        assert normalize_error(outer).kind is NovaErrorKind.INTERNAL

    def test_cyclic_cause_chain_terminates(self):
        """循环 cause 链（respx SideEffectError 真实形状 A↔B）——环保护，
        找到传输错误且**终止**，不死循环。"""
        wrapper = Exception("sdk wrap")
        transport = httpx.ConnectError("refused")
        wrapper.__cause__ = transport
        transport.__cause__ = wrapper
        result = normalize_error(wrapper)
        assert result.kind is NovaErrorKind.HTTP_CONNECTION_FAILED
        assert result.retryable is True

    def test_cyclic_cause_chain_plain(self):
        """循环链无任何可分类节点 → INTERNAL（同样要终止）。"""
        a = Exception("a")
        b = Exception("b")
        a.__cause__ = b
        b.__cause__ = a
        assert normalize_error(a).kind is NovaErrorKind.INTERNAL


class TestNormalizedErrorFields:
    def test_retry_after_and_status(self):
        result = normalize_error(
            _StatusError(
                "slow down", status_code=429, headers={"retry-after-ms": "1500"}
            )
        )
        assert result.retry_after_ms == 1500.0
        assert result.http_status == 429

    def test_message_truncated_at_2kb(self):
        result = normalize_error(Exception("x" * 5000))
        assert len(result.message) < 2100
        assert "truncated" in result.message

    def test_domain_passthrough(self):
        result = normalize_error(ValueError("x"), domain="stream")
        assert result.domain == "stream"

    def test_frozen(self):
        result = normalize_error(ValueError("x"))
        with pytest.raises(Exception):
            result.kind = NovaErrorKind.INTERNAL  # type: ignore[misc]


class TestHeaderHelpers:
    def test_get_header_dict_case_insensitive(self):
        assert get_header({"Retry-After-MS": "250"}, "retry-after-ms") == "250"

    def test_get_header_httpx_headers(self):
        headers = httpx.Headers({"retry-after": "2"})
        assert get_header(headers, "Retry-After") == "2"

    def test_get_header_none(self):
        assert get_header(None, "x") is None

    def test_extract_retry_after_ms_numeric(self):
        assert extract_retry_after_ms({"retry-after-ms": "250"}) == 250.0
        assert extract_retry_after_ms({"retry-after": "2"}) == 2000.0

    def test_extract_retry_after_ms_unparsable(self):
        assert extract_retry_after_ms({"retry-after": "not-a-number-or-date"}) is None
        assert extract_retry_after_ms(None) is None


class TestMessageRedaction:
    """归一消息的出口清洗（redact_secrets 兜底——铁律 6 的最后一道闸）。"""

    def test_bearer_token_redacted(self):
        token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2Q"
        result = normalize_error(RuntimeError(f"provider echoed Bearer {token}"))
        assert token not in result.message
        assert "[REDACTED_SECRET]" in result.message

    def test_jwt_redacted(self):
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ4In0.SflKxwRJSMeKKF2QT4"
        result = normalize_error(RuntimeError(f"token endpoint said: {jwt}"))
        assert jwt not in result.message

    def test_assignment_form_redacted(self):
        result = normalize_error(
            RuntimeError("bad config: access_token=abcdefgh12345678 was rejected")
        )
        assert "abcdefgh12345678" not in result.message

    def test_classification_uses_original_text(self):
        """清洗只作用于展示消息——分类词表吃原文（429 + quota 文案仍分流正确）。"""
        result = normalize_error(
            RuntimeError("insufficient_quota: access_token=abcdefgh12345678")
        )
        assert result.kind is NovaErrorKind.USAGE_LIMIT_EXCEEDED
        assert "abcdefgh12345678" not in result.message

    def test_plain_message_unchanged(self):
        result = normalize_error(RuntimeError("plain failure, nothing secret"))
        assert result.message == "plain failure, nothing secret"

    def test_non_exception_input_is_also_redacted(self):
        secret = "sk-live-nonexception-1234567890"
        result = normalize_error({"message": f"Bearer {secret}"})
        assert secret not in result.message

    def test_non_finite_retry_after_falls_back(self):
        assert extract_retry_after_ms({"retry-after": "NaN"}) is None
        assert extract_retry_after_ms({"retry-after": "Infinity"}) is None


class TestChainWalking:
    """归一的链走对称性（kind/http_status/retry_after_ms 同一姿势沿 cause 链）。"""

    def test_retry_after_ms_walks_cause_chain(self):
        """包装后的限流错误：retry_after_ms 沿链捞到底层 headers。"""
        inner = _StatusError(
            "slow down", status_code=429, headers={"retry-after-ms": "1500"}
        )
        wrapper = Exception("outer wrap")
        wrapper.__cause__ = inner
        result = normalize_error(wrapper)
        assert result.kind is NovaErrorKind.RATE_LIMITED
        assert result.retry_after_ms == 1500.0

    def test_domain_falls_back_to_models_error_code(self):
        """domain 缺省时读领域错误的 code（ModelsError 六域——谁报的）。"""
        from nova_ai.auth.resolve import ModelsError

        result = normalize_error(ModelsError("oauth", "token refresh failed"))
        assert result.domain == "oauth"

    def test_domain_param_wins_over_code(self):
        from nova_ai.auth.resolve import ModelsError

        result = normalize_error(ModelsError("oauth", "x"), domain="stream")
        assert result.domain == "stream"


class TestLoginRequiredError:
    """无凭据守卫的自声明 kind（LOGIN_REQUIRED 零产出路径的回归锁）。"""

    def test_self_declared_kind_and_actionable_message(self):
        from nova_ai.utils.errors import LoginRequiredError

        err = LoginRequiredError("kimi-coding")
        assert err.nova_error_kind is NovaErrorKind.LOGIN_REQUIRED
        # 可行动文案（铁律 7）：指出下一步
        assert "/login" in str(err)
        assert "kimi-coding" in str(err)

    def test_normalize_picks_login_required(self):
        from nova_ai.utils.errors import LoginRequiredError

        result = normalize_error(LoginRequiredError("openai"))
        assert result.kind is NovaErrorKind.LOGIN_REQUIRED
        assert result.retryable is False


class TestHeadersOf:
    """``headers_of`` 边界归一（SDK 错误形状知识的唯一归档点——F1 回归锁）。"""

    def test_reads_own_headers_attr(self):
        """自家错误形状（直接带 .headers）优先。"""

        class CustomError(Exception):
            headers = {"retry-after-ms": "100"}

        assert headers_of(CustomError()) == {"retry-after-ms": "100"}

    def test_reads_sdk_error_response_headers(self):
        """openai/anthropic 的 ``APIStatusError`` 实例无 ``.headers``——
        头在 ``.response.headers``（SDK 源码实证，归一函数注释在案）。"""
        for cls in (openai.APIStatusError, anthropic.APIStatusError):
            response = httpx.Response(
                429,
                headers={"retry-after-ms": "2000"},
                request=httpx.Request("POST", "http://x"),
            )
            error = cls("rate limited", response=response, body=None)
            assert headers_of(error) is response.headers

    def test_plain_error_returns_none(self):
        """无头可读的普通异常 → None（显式缺席语义）。"""
        assert headers_of(RuntimeError("x")) is None
