"""客户端创建（对齐 TS ``createClient``）。

重试纪律：客户端一律 ``max_retries=0``——SDK 内建重试的退避睡眠无视
abort；重试归 ``_shared/retry.py`` 的 ``retry_provider_request`` 接管。

鉴权三形态（对齐 TS）：API key（``x-api-key`）/ OAuth token（``sk-ant-oat*``
→ Bearer + Claude Code 身份头）/ Copilot（Bearer + 选择性 beta）。
"""

import json
from typing import Any, Dict, List, NamedTuple, Optional, Tuple, cast

import httpx2
from anthropic import AsyncAnthropic, omit
from nova_protocol import ClientIdentity, Context, Model, ProviderEnv, ProviderHeaders

from ...utils.errors import LoginRequiredError
from ...utils.provider_env import get_provider_env_value
from .._shared.copilot_headers import (
    build_copilot_dynamic_headers,
    has_copilot_vision_input,
)
from .._shared.identity import get_identity_headers
from .._shared.transcript import get_current_tools, normalize_context
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
_CLAUDE_CODE_VERSION = "2.1.280"


def _sdk_headers(headers: ProviderHeaders):
    """None → SDK omit：显式抑制同名默认头，而非删除后让 SDK 再补回。"""
    # SDK 运行时支持 Omit，但其 default_headers 注解仍写纯 str（上游已知 fudge）。
    return cast(
        "dict[str, str]",
        {key: (omit if value is None else value) for key, value in headers.items()},
    )


def _suppress_transport_defaults(
    client: AsyncAnthropic, headers: ProviderHeaders
) -> AsyncAnthropic:
    """同时移除底层 httpx2 client 的同名默认头（SDK omit 管不到这一层）。"""
    for key, value in headers.items():
        if value is None:
            # SDK 没有公开 transport-headers 写口；这是 httpx2 默认头的唯一落点。
            client._client.headers.pop(key.lower(), None)
    return client


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
    headers 未自带鉴权（authorization / x-api-key / cf-aig-authorization）
    即报错——协议层不读环境变量，key 必须由上游（ModelsManager.applyAuth /
    调用方）注入。"""
    if api_key:
        return
    if (
        _has_header(headers, "authorization")
        or _has_header(headers, "x-api-key")
        or _has_header(headers, "cf-aig-authorization")
    ):
        return
    raise LoginRequiredError(provider)


def is_oauth_token(api_key: str) -> bool:
    """Anthropic OAuth token 识别（对齐 TS isOAuthToken）。"""
    return "sk-ant-oat" in api_key


def anthropic_federation_config(
    model: Model,
    api_key: Optional[str],
    headers: Optional[ProviderHeaders],
    env: Optional[ProviderEnv],
) -> Optional[Dict[str, Any]]:
    """读取 Anthropic workload identity federation 配置（pi 同名能力对位）。

    仅在 anthropic 本家且没有任何显式凭据时启用；三个必需变量缺一即不
    启用，让调用方继续走普通的缺凭据报错。
    """
    if (
        model.provider != "anthropic"
        or api_key
        or _has_header(headers, "authorization")
        or _has_header(headers, "x-api-key")
        or _has_header(headers, "cf-aig-authorization")
    ):
        return None
    rule_id = get_provider_env_value("ANTHROPIC_FEDERATION_RULE_ID", env)
    organization_id = get_provider_env_value("ANTHROPIC_ORGANIZATION_ID", env)
    token_file = get_provider_env_value("ANTHROPIC_IDENTITY_TOKEN_FILE", env)
    if not rule_id or not organization_id or not token_file:
        return None
    authentication: Dict[str, Any] = {
        "type": "oidc_federation",
        "federation_rule_id": rule_id,
        "identity_token": {"source": "file", "path": token_file},
    }
    service_account_id = get_provider_env_value("ANTHROPIC_SERVICE_ACCOUNT_ID", env)
    if service_account_id:
        authentication["service_account_id"] = service_account_id
    config: Dict[str, Any] = {
        "organization_id": organization_id,
        "authentication": authentication,
    }
    workspace_id = get_provider_env_value("ANTHROPIC_WORKSPACE_ID", env)
    if workspace_id:
        config["workspace_id"] = workspace_id
    return config


def build_beta_features(
    model: Model,
    context: Context,
    compat: ResolvedAnthropicCompat,
    *,
    interleaved_thinking: bool = True,
    thinking_enabled: bool = False,
    session_id: Optional[str] = None,
) -> List[str]:
    """beta 特征列表（对齐 TS getBetaFeatures——保序去重）。

    线程指纹（``hash_thread_prefix``）与请求头共用同一份列表，两侧
    必须同构——任何新增 beta 都进本函数。
    """
    beta_features: List[str] = []
    # fine-grained 门（对齐 TS shouldUseFineGrainedToolStreamingBeta）：
    # 当前工具集非空（含中途 system 消息引入的——get_current_tools）
    # 且端点不接受 eager_input_streaming
    if not compat.supports_eager_tool_input_streaming and get_current_tools(
        normalize_context(context)
    ):
        beta_features.append(FINE_GRAINED_TOOL_STREAMING_BETA)
    # interleaved 门（对齐 TS）：reasoning 模型 + 思考**显式**开启 +
    # interleaved 未关 + 非 adaptive（adaptive 内置，跳过 beta 头）
    if (
        model.reasoning
        and thinking_enabled
        and interleaved_thinking
        and not compat.force_adaptive_thinking
    ):
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


class BetaResolution(NamedTuple):
    """最终 beta 列表及注入 client 需要下发的显式头覆盖。"""

    values: List[str]
    overridden: bool
    raw_override: Optional[str]


def resolve_beta_features(
    model: Model,
    options_headers: Optional[ProviderHeaders],
    generated: List[str],
) -> BetaResolution:
    """model/options 中的 anthropic-beta 可替换或清空生成列表（后者覆盖前者）。"""
    overridden = False
    raw: Optional[str] = None
    for headers in (model.headers, options_headers):
        if not headers:
            continue
        for key, value in headers.items():
            if key.lower() == "anthropic-beta":
                overridden = True
                raw = value
    values = (
        generated
        if not overridden
        else ([] if raw is None else [v.strip() for v in raw.split(",") if v.strip()])
    )
    return BetaResolution(values=values, overridden=overridden, raw_override=raw)


_FEDERATION_CLIENT: Optional[Tuple[Tuple[str, str, Any], AsyncAnthropic]] = None


def _federation_base_client(
    model: Model, federation: Dict[str, Any], http_client: Any
) -> AsyncAnthropic:
    """按配置保留 federation 基座；请求级 client 只共享其 token cache。"""
    global _FEDERATION_CLIENT
    key = (model.base_url, json.dumps(federation, sort_keys=True), http_client)
    if _FEDERATION_CLIENT is None or _FEDERATION_CLIENT[0] != key:
        client = AsyncAnthropic(
            api_key=None,
            auth_token=None,
            config=federation,
            base_url=model.base_url,
            http_client=http_client,
            max_retries=0,
        )
        _FEDERATION_CLIENT = (key, client)
    return _FEDERATION_CLIENT[1]


def create_client(
    model: Model,
    context: Context,
    api_key: Optional[str] = None,
    options_headers: Optional[ProviderHeaders] = None,
    session_id: Optional[str] = None,
    compat: Optional[ResolvedAnthropicCompat] = None,
    interleaved_thinking: bool = True,
    thinking_enabled: bool = False,
    http_client=None,
    federation: Optional[Dict[str, Any]] = None,
    identity: Optional[ClientIdentity] = None,
) -> Tuple[AsyncAnthropic, bool]:
    """创建 Anthropic 客户端（对齐 TS createClient），返回 ``(client, is_oauth)``。

    ``http_client``：httpx2 客户端注入缝（测试经 MockTransport 接管传输层；
    生产恒 None 由 SDK 自造）。
    """
    headers: ProviderHeaders = {
        **get_identity_headers(model, identity),
        "accept": "application/json",
    }
    beta_features: List[str] = []
    if compat is not None:
        beta_features = build_beta_features(
            model,
            context,
            compat,
            interleaved_thinking=interleaved_thinking,
            thinking_enabled=thinking_enabled,
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
        # 头名随格式（对齐 TS——openrouter 发 x-session-id，否则 x-session-affinity）
        if compat.session_affinity_format == "openrouter":
            headers["x-session-id"] = session_id
        else:
            headers["x-session-affinity"] = session_id

    if options_headers:
        headers.update(options_headers)

    if model.provider == "github-copilot":
        if api_key:
            client = AsyncAnthropic(
                api_key=None,
                auth_token=api_key,
                base_url=model.base_url,
                default_headers=_sdk_headers(headers),
                http_client=http_client,
                max_retries=0,
            )
        else:
            # header-owned Copilot 鉴权也必须显式封堵 SDK 的 env/default
            # 凭据链，否则 Anthropic 的 ambient key 会被发到另一端点。
            has_authorization = _has_header(headers, "authorization")
            has_x_api_key = _has_header(headers, "x-api-key")
            if not has_authorization:
                headers["authorization"] = None
            if not has_x_api_key:
                headers["x-api-key"] = None
            client = AsyncAnthropic(
                api_key="unused",
                auth_token="unused",
                base_url=model.base_url,
                default_headers=_sdk_headers(headers),
                http_client=http_client,
                max_retries=0,
            )
        return _suppress_transport_defaults(client, headers), False

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
        # 显式 model/options 头最后生效（对齐 pi mergeHeaders 顺序）：
        # 可替换/清空 beta，也可覆盖 CLI UA；OAuth 身份只提供默认值。
        if model.headers:
            headers.update(model.headers)
        if options_headers:
            headers.update(options_headers)
        client = AsyncAnthropic(
            api_key=None,
            auth_token=api_key,
            base_url=model.base_url,
            default_headers=_sdk_headers(headers),
            http_client=http_client,
            max_retries=0,
        )
        return _suppress_transport_defaults(client, headers), True

    if federation is not None:
        # 显式 config 阻断其他环境凭据。请求级 client 只共享基座的 token
        # cache，不再 with_options 共享底层 headers——否则一次请求的 suppress
        # 会永久污染后续请求。测试注入 client 且需要删头时，仅复用其 transport。
        base = _federation_base_client(model, federation, http_client)
        request_http_client = http_client
        if http_client is not None and any(value is None for value in headers.values()):
            request_http_client = httpx2.AsyncClient(transport=http_client._transport)
        client = AsyncAnthropic(
            api_key=None,
            auth_token=None,
            config=federation,
            base_url=model.base_url,
            default_headers=_sdk_headers(headers),
            http_client=request_http_client,
            max_retries=0,
        )
        client._token_cache = base._token_cache
        return _suppress_transport_defaults(client, headers), False

    # SDK 隐式凭据链封堵：占位值只用于阻断环境读取，绝不能实际过线。
    # 只有未传真实 api_key 时，才为 Bearer/CF 自定义路径抑制 SDK 自动
    # 生成的 x-api-key；双显式凭据保持调用方组合，不做静默删除。
    has_authorization = _has_header(headers, "authorization")
    has_x_api_key = _has_header(headers, "x-api-key")
    has_cf_auth = _has_header(headers, "cf-aig-authorization")
    # 与占位生成共用真值语义：空字符串同样不是真实 key。
    if not api_key and (has_authorization or has_cf_auth) and not has_x_api_key:
        headers["x-api-key"] = None
    if (has_x_api_key or has_cf_auth) and not has_authorization:
        headers["authorization"] = None
    headers_carry_auth = has_authorization or has_x_api_key or has_cf_auth
    sdk_api_key = api_key or ("unused" if headers_carry_auth else None)
    client = AsyncAnthropic(
        api_key=sdk_api_key,
        base_url=model.base_url,
        default_headers=_sdk_headers(headers),
        http_client=http_client,
        max_retries=0,
    )
    return _suppress_transport_defaults(client, headers), False


__all__ = [
    "anthropic_federation_config",
    "assert_request_auth",
    "build_beta_features",
    "create_client",
    "is_oauth_token",
    "resolve_beta_features",
]
