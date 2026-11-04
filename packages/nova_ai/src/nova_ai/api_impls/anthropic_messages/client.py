"""客户端创建（对齐 TS ``createClient``）。

重试纪律：客户端一律 ``max_retries=0``——SDK 内建重试的退避睡眠无视
abort；重试归 ``_shared/retry.py`` 的 ``retry_provider_request`` 接管。

鉴权三形态（对齐 TS）：API key（``x-api-key`）/ OAuth token（``sk-ant-oat*``
→ Bearer + Claude Code 身份头）/ Copilot（Bearer + 选择性 beta）。
"""

from typing import List, Optional, Tuple

from anthropic import AsyncAnthropic
from nova_protocol import Context, Model, ProviderHeaders

from ...utils.errors import LoginRequiredError
from .._shared.copilot_headers import (
    build_copilot_dynamic_headers,
    has_copilot_vision_input,
)
from .._shared.user_agent import get_nova_user_agent
from .compat import ResolvedAnthropicCompat
from .params import (
    INLINE_TOOLS_BETA,
    MID_CONVERSATION_OUTPUT_CONFIG_BETA,
    THINKING_BINDING_CONTROLS_BETA,
    resolve_native_tool_changes,
)
from .threads import MESSAGE_THREADS_BETA

FINE_GRAINED_TOOL_STREAMING_BETA = "fine-grained-tool-streaming-2025-05-14"
INTERLEAVED_THINKING_BETA = "interleaved-thinking-2025-05-14"
# 服务端 refusal fallback（对齐 TS——名单非空才发，无许可目标时端点拒收字段）
SERVER_SIDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Claude Code 身份（OAuth 令牌的强制身份头——对齐 TS claudeCodeVersion）
_CLAUDE_CODE_VERSION = "2.1.75"


def _has_header(headers: Optional[ProviderHeaders], name: str) -> bool:
    """检查 headers 中是否已存在非空指定头部（对齐 TS hasHeader）。"""
    if not headers:
        return False
    expected = name.lower()
    for key, value in headers.items():
        if key.lower() == expected and value is not None and value.strip():
            return True
    return False


def assert_request_auth(
    provider: str,
    api_key: Optional[str],
    headers: Optional[ProviderHeaders],
) -> None:
    """鉴权 fail-fast 守卫（对齐 TS assertRequestAuth）：api key 缺失且
    headers 未自带鉴权（authorization / x-api-key）即报错——协议层不读
    环境变量，key 必须由上游（ModelsManager.applyAuth / 调用方）注入。"""
    if api_key:
        return
    if _has_header(headers, "authorization") or _has_header(headers, "x-api-key"):
        return
    raise LoginRequiredError(provider)


def is_oauth_token(api_key: str) -> bool:
    """Anthropic OAuth token 识别（对齐 TS isOAuthToken）。"""
    return "sk-ant-oat" in api_key


def build_beta_features(
    model: Model,
    context: Context,
    compat: ResolvedAnthropicCompat,
    *,
    interleaved_thinking: bool = True,
    session_id: Optional[str] = None,
) -> List[str]:
    """beta 特征列表（对齐 TS getBetaFeatures——保序去重）。

    线程指纹（``hash_thread_prefix``）与请求头共用同一份列表，两侧
    必须同构——任何新增 beta 都进本函数。
    """
    beta_features: List[str] = []
    if not compat.supports_eager_tool_input_streaming and context.tools:
        beta_features.append(FINE_GRAINED_TOOL_STREAMING_BETA)
    if interleaved_thinking and not compat.force_adaptive_thinking:
        beta_features.append(INTERLEAVED_THINKING_BETA)
    if compat.supports_mid_convo_effort:
        beta_features.extend(
            [MID_CONVERSATION_OUTPUT_CONFIG_BETA, THINKING_BINDING_CONTROLS_BETA]
        )
    # INLINE_TOOLS（对齐 TS）：nativeToolChanges 形态才发
    if resolve_native_tool_changes(context, compat):
        beta_features.append(INLINE_TOOLS_BETA)
    # message threads（对齐 TS：会话线程激活即带——端点拒过（"off"）的
    # 会话仍带，关停重发正是靠这个 beta 被接受）
    if session_id is not None and compat.supports_message_threads:
        beta_features.append(MESSAGE_THREADS_BETA)
    # 服务端 refusal fallback（对齐 TS shouldUseServerSideFallbackBeta）
    if compat.allowed_fallback_models:
        beta_features.append(SERVER_SIDE_FALLBACK_BETA)
    return list(dict.fromkeys(beta_features))


def create_client(
    model: Model,
    context: Context,
    api_key: Optional[str] = None,
    options_headers: Optional[ProviderHeaders] = None,
    session_id: Optional[str] = None,
    compat: Optional[ResolvedAnthropicCompat] = None,
    interleaved_thinking: bool = True,
    http_client=None,
) -> Tuple[AsyncAnthropic, bool]:
    """创建 Anthropic 客户端（对齐 TS createClient），返回 ``(client, is_oauth)``。

    ``http_client``：httpx2 客户端注入缝（测试经 MockTransport 接管传输层；
    生产恒 None 由 SDK 自造）。
    """
    headers: ProviderHeaders = {
        "User-Agent": get_nova_user_agent(),
        "accept": "application/json",
    }
    beta_features: List[str] = []
    if compat is not None:
        beta_features = build_beta_features(
            model,
            context,
            compat,
            interleaved_thinking=interleaved_thinking,
            session_id=session_id,
        )
        if beta_features:
            headers["anthropic-beta"] = ",".join(beta_features)

    # Copilot：Bearer 鉴权 + 选择性 beta（对齐 TS copilot 分支）
    if model.provider == "github-copilot":
        dynamic = build_copilot_dynamic_headers(
            context.messages, has_copilot_vision_input(context.messages)
        )
        headers.update(dynamic)

    if model.headers:
        headers.update(model.headers)

    if session_id and compat is not None and compat.send_session_affinity_headers:
        headers["x-session-affinity"] = session_id

    if options_headers:
        headers.update(options_headers)

    if api_key and is_oauth_token(api_key):
        # OAuth：Bearer + Claude Code 身份头（对齐 TS OAuth 分支）
        headers.update(
            {
                "anthropic-beta": ",".join(
                    ["claude-code-20250219", "oauth-2025-04-20", *beta_features]
                ),
                "user-agent": f"claude-cli/{_CLAUDE_CODE_VERSION}",
                "x-app": "cli",
            }
        )
        return (
            AsyncAnthropic(
                api_key=None,
                auth_token=api_key,
                base_url=model.base_url,
                default_headers={k: v for k, v in headers.items() if v is not None},
                http_client=http_client,
                max_retries=0,
            ),
            True,
        )

    client = AsyncAnthropic(
        api_key=api_key,
        base_url=model.base_url,
        default_headers={k: v for k, v in headers.items() if v is not None},
        http_client=http_client,
        max_retries=0,
    )
    return client, False


__all__ = ["assert_request_auth", "create_client", "is_oauth_token"]
