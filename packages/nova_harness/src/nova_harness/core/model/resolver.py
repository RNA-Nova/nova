"""Model resolution, scoping, and initial selection.

与 TypeScript 端 ``core/model-resolver.ts`` 对齐，把模型选择、思考级别解析
从 ``sdk.py`` 中拆出，便于独立测试和复用。
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from nova_agent import ModelThinkingLevel
from nova_ai import Model, clamp_thinking_level

from nova_harness.core.config.defaults import DEFAULT_THINKING_LEVEL
from nova_harness.core.types.session.model import ScopedModelConfig

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

#: 各已知 provider 的默认模型 ID，用于 fallback 时优先选择。
DEFAULT_MODEL_PER_PROVIDER: Dict[str, str] = {
    "amazon-bedrock": "us.anthropic.claude.opus-4-6-v1",
    "ant-ling": "Ring-2.6-1T",
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-5.4",
    "azure-openai-responses": "gpt-5.4",
    "openai-codex": "gpt-5.5",
    "nvidia": "nvidia/nemotron-3-super-120b-a12b",
    "deepseek": "deepseek-v4-pro",
    "google": "gemini-3.1-pro-preview",
    "google-vertex": "gemini-3.1-pro-preview",
    "github-copilot": "gpt-5.4",
    "openrouter": "moonshotai/kimi-k2.6",
    "vercel-ai-gateway": "zai/glm-5.1",
    "xai": "grok-4.20-0309-reasoning",
    "groq": "openai/gpt-oss-120b",
    "cerebras": "zai-glm-4.7",
    "zai": "glm-5.1",
    "zai-coding-cn": "glm-5.1",
    "mistral": "devstral-medium-latest",
    "minimax": "MiniMax-M2.7",
    "minimax-cn": "MiniMax-M2.7",
    "moonshotai": "kimi-k2.6",
    "moonshotai-cn": "kimi-k2.6",
    "huggingface": "moonshotai/Kimi-K2.6",
    "fireworks": "accounts/fireworks/models/kimi-k2p6",
    "together": "moonshotai/Kimi-K2.6",
    "opencode": "kimi-k2.6",
    "opencode-go": "kimi-k2.6",
    "kimi-coding": "kimi-for-coding",
    "cloudflare-workers-ai": "@cf/moonshotai/kimi-k2.6",
    "cloudflare-ai-gateway": "workers-ai/@cf/moonshotai/kimi-k2.6",
    "xiaomi": "mimo-v2.5-pro",
    "xiaomi-token-plan-cn": "mimo-v2.5-pro",
    "xiaomi-token-plan-ams": "mimo-v2.5-pro",
    "xiaomi-token-plan-sgp": "mimo-v2.5-pro",
}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _is_alias(model_id: str) -> bool:
    """判断模型 ID 是否为别名（无日期后缀或 ``-latest`` 结尾）。"""
    if model_id.endswith("-latest"):
        return True
    import re

    return not re.search(r"-\d{8}$", model_id)


def _is_valid_thinking_level(value: Optional[str]) -> bool:
    """检查字符串是否为有效的思考级别。"""
    if value is None:
        return False
    return value in {level.value for level in ModelThinkingLevel}


def _provider_str(provider: Any) -> str:
    """把 provider（可能是 str 或 str Enum）统一转为字符串值。"""
    if isinstance(provider, Enum):
        return str(provider.value)
    return str(provider)


def _models_are_equal(a: Model, b: Model) -> bool:
    """比较两个模型是否等价（provider + id）。"""
    return a.provider == b.provider and a.id == b.id


# ---------------------------------------------------------------------------
# Resolution primitives
# ---------------------------------------------------------------------------


def find_exact_model_reference_match(
    model_reference: str,
    available_models: List[Model],
) -> Optional[Model]:
    """按精确引用匹配模型。

    支持：
    - ``provider/modelId`` 完整格式（大小写不敏感）
    - 仅 ``modelId``（当不跨 provider 歧义时）
    """
    trimmed = model_reference.strip()
    if not trimmed:
        return None

    normalized = trimmed.lower()

    canonical_matches = [
        m
        for m in available_models
        if f"{_provider_str(m.provider)}/{m.id}".lower() == normalized
    ]
    if len(canonical_matches) == 1:
        return canonical_matches[0]
    if len(canonical_matches) > 1:
        return None

    slash_index = trimmed.find("/")
    if slash_index != -1:
        provider = trimmed[:slash_index].strip()
        model_id = trimmed[slash_index + 1 :].strip()
        if provider and model_id:
            provider_matches = [
                m
                for m in available_models
                if m.provider.lower() == provider.lower()
                and m.id.lower() == model_id.lower()
            ]
            if len(provider_matches) == 1:
                return provider_matches[0]
            if len(provider_matches) > 1:
                return None

    id_matches = [m for m in available_models if m.id.lower() == normalized]
    return id_matches[0] if len(id_matches) == 1 else None


def _try_match_model(
    model_pattern: str,
    available_models: List[Model],
) -> Optional[Model]:
    """模糊匹配模型：先精确匹配，再按 ID / name 子串匹配，优先别名。"""
    exact = find_exact_model_reference_match(model_pattern, available_models)
    if exact is not None:
        return exact

    pattern_lower = model_pattern.lower()
    matches = [
        m
        for m in available_models
        if pattern_lower in m.id.lower()
        or (m.name is not None and pattern_lower in m.name.lower())
    ]
    if not matches:
        return None

    aliases = [m for m in matches if _is_alias(m.id)]
    if aliases:
        aliases.sort(key=lambda m: m.id, reverse=True)
        return aliases[0]

    dated = [m for m in matches if not _is_alias(m.id)]
    dated.sort(key=lambda m: m.id, reverse=True)
    return dated[0]


def _build_fallback_model(
    provider: str,
    model_id: str,
    available_models: List[Model],
) -> Optional[Model]:
    """为已知 provider 构造一个自定义 model id 的 fallback 模型。"""
    provider_models = [m for m in available_models if m.provider == provider]
    if not provider_models:
        return None

    default_id = DEFAULT_MODEL_PER_PROVIDER.get(provider)
    base = (
        next((m for m in provider_models if m.id == default_id), None)
        or provider_models[0]
    )
    return base.model_copy(update={"id": model_id, "name": model_id})


@dataclass(frozen=True)
class ParsedModelResult:
    """``parse_model_pattern`` 结果。"""

    model: Optional[Model] = None
    thinking_level: Optional[ModelThinkingLevel] = None
    warning: Optional[str] = None


def parse_model_pattern(
    pattern: str,
    available_models: List[Model],
    *,
    allow_invalid_thinking_level_fallback: bool = True,
) -> ParsedModelResult:
    """解析模型模式，支持 ``model:thinking`` 后缀。

    算法：
    1. 先整体匹配；
    2. 未匹配且包含冒号时，从最后一个冒号切分：
       - 后缀是有效 thinking level 则递归前缀并使用该级别；
       - 否则在允许 fallback 时递归前缀并生成 warning。
    """
    exact = _try_match_model(pattern, available_models)
    if exact is not None:
        return ParsedModelResult(model=exact)

    last_colon = pattern.rfind(":")
    if last_colon == -1:
        return ParsedModelResult()

    prefix = pattern[:last_colon]
    suffix = pattern[last_colon + 1 :]

    if _is_valid_thinking_level(suffix):
        inner = parse_model_pattern(prefix, available_models)
        if inner.model is None:
            return inner
        return ParsedModelResult(
            model=inner.model,
            thinking_level=inner.warning is None and ModelThinkingLevel(suffix) or None,
            warning=inner.warning,
        )

    if not allow_invalid_thinking_level_fallback:
        return ParsedModelResult()

    inner = parse_model_pattern(prefix, available_models)
    if inner.model is None:
        return inner
    return ParsedModelResult(
        model=inner.model,
        warning=(
            f'Invalid thinking level "{suffix}" in pattern "{pattern}". '
            "Using default instead."
        ),
    )


# ---------------------------------------------------------------------------
# Scoped models
# ---------------------------------------------------------------------------


def resolve_model_scope(
    patterns: List[str],
    model_runtime: Any,
) -> List[ScopedModelConfig]:
    """把一组模型模式解析为带可选 thinking level 的作用域模型列表。

    与 TS 对齐，只包含有鉴权的可用模型（``get_available_snapshot``）。
    """
    available_models = model_runtime.get_available_snapshot()
    scoped: List[ScopedModelConfig] = []

    for pattern in patterns:
        if "*" in pattern or "?" in pattern or "[" in pattern:
            colon_idx = pattern.rfind(":")
            glob_pattern = pattern
            thinking_level: Optional[ModelThinkingLevel] = None
            if colon_idx != -1:
                suffix = pattern[colon_idx + 1 :]
                if _is_valid_thinking_level(suffix):
                    thinking_level = ModelThinkingLevel(suffix)
                    glob_pattern = pattern[:colon_idx]

            for m in available_models:
                full_id = f"{_provider_str(m.provider)}/{m.id}"
                if fnmatch.fnmatchcase(
                    full_id.lower(), glob_pattern.lower()
                ) or fnmatch.fnmatchcase(m.id.lower(), glob_pattern.lower()):
                    if not any(_models_are_equal(sm.model, m) for sm in scoped):
                        scoped.append(
                            ScopedModelConfig(model=m, thinking_level=thinking_level)
                        )
            continue

        parsed = parse_model_pattern(pattern, available_models)
        if parsed.model is not None and not any(
            _models_are_equal(sm.model, parsed.model) for sm in scoped
        ):
            scoped.append(
                ScopedModelConfig(
                    model=parsed.model,
                    thinking_level=parsed.thinking_level,
                )
            )

    return scoped


# ---------------------------------------------------------------------------
# CLI model resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolveCliModelResult:
    """CLI 模型解析结果。"""

    model: Optional[Model] = None
    thinking_level: Optional[ModelThinkingLevel] = None
    warning: Optional[str] = None
    error: Optional[str] = None


def resolve_cli_model(
    *,
    cli_provider: Optional[str],
    cli_model: Optional[str],
    model_runtime: Any,
) -> ResolveCliModelResult:
    """根据 CLI 参数解析单个模型。

    支持：
    - ``--provider <p> --model <pattern>``
    - ``--model <p>/<pattern>``
    - 模糊匹配
    """
    if not cli_model:
        return ResolveCliModelResult()

    available_models = model_runtime.get_all()
    if not available_models:
        return ResolveCliModelResult(
            error="No models available. Check your installation or add models to models.json."
        )

    provider_map = {m.provider.lower(): m.provider for m in available_models}
    provider = provider_map.get(cli_provider.lower()) if cli_provider else None
    if cli_provider and provider is None:
        return ResolveCliModelResult(
            error=f'Unknown provider "{cli_provider}". Use --list-models to see available providers/models.'
        )

    pattern = cli_model
    inferred_provider = False

    if provider is None:
        slash_index = cli_model.find("/")
        if slash_index != -1:
            maybe_provider = cli_model[:slash_index]
            canonical = provider_map.get(maybe_provider.lower())
            if canonical:
                provider = canonical
                pattern = cli_model[slash_index + 1 :]
                inferred_provider = True

    if provider is None:
        lower = cli_model.lower()
        exact = next(
            (
                m
                for m in available_models
                if m.id.lower() == lower
                or f"{_provider_str(m.provider)}/{m.id}".lower() == lower
            ),
            None,
        )
        if exact is not None:
            return ResolveCliModelResult(model=exact)

    if provider is not None and cli_provider:
        prefix = f"{_provider_str(provider)}/"
        if cli_model.lower().startswith(prefix.lower()):
            pattern = cli_model[len(prefix) :]

    candidates = (
        [m for m in available_models if m.provider == provider]
        if provider
        else available_models
    )
    parsed = parse_model_pattern(
        pattern,
        candidates,
        allow_invalid_thinking_level_fallback=False,
    )

    if parsed.model is not None:
        if inferred_provider:
            raw_exact = [
                m
                for m in available_models
                if m.id.lower() == cli_model.lower()
                and not _models_are_equal(m, parsed.model)
            ]
            if (
                raw_exact
                and hasattr(model_runtime, "has_configured_auth")
                and not model_runtime.has_configured_auth(parsed.model)
            ):
                authenticated = [
                    m for m in raw_exact if model_runtime.has_configured_auth(m)
                ]
                if len(authenticated) == 1:
                    return ResolveCliModelResult(model=authenticated[0])
        return ResolveCliModelResult(
            model=parsed.model,
            thinking_level=parsed.thinking_level,
            warning=parsed.warning,
        )

    if inferred_provider:
        lower = cli_model.lower()
        exact = next(
            (
                m
                for m in available_models
                if m.id.lower() == lower
                or f"{_provider_str(m.provider)}/{m.id}".lower() == lower
            ),
            None,
        )
        if exact is not None:
            return ResolveCliModelResult(model=exact)

        fallback = parse_model_pattern(
            cli_model,
            available_models,
            allow_invalid_thinking_level_fallback=False,
        )
        if fallback.model is not None:
            return ResolveCliModelResult(
                model=fallback.model,
                thinking_level=fallback.thinking_level,
                warning=fallback.warning,
            )

    if provider is not None:
        fallback_pattern = pattern
        fallback_thinking: Optional[ModelThinkingLevel] = None
        last_colon = pattern.rfind(":")
        if last_colon != -1:
            suffix = pattern[last_colon + 1 :]
            if _is_valid_thinking_level(suffix):
                fallback_pattern = pattern[:last_colon]
                fallback_thinking = ModelThinkingLevel(suffix)

        fallback_model = _build_fallback_model(
            provider, fallback_pattern, available_models
        )
        if fallback_model is not None:
            return ResolveCliModelResult(
                model=fallback_model,
                thinking_level=fallback_thinking,
                warning=f'Model "{fallback_pattern}" not found for provider "{_provider_str(provider)}". Using custom model id.',
            )

    display = f"{_provider_str(provider)}/{pattern}" if provider else cli_model
    return ResolveCliModelResult(
        error=f'Model "{display}" not found. Use --list-models to see available models.'
    )


# ---------------------------------------------------------------------------
# Initial model selection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InitialModelResult:
    """初始模型解析结果。"""

    model: Optional[Model]
    thinking_level: Optional[ModelThinkingLevel] = None
    fallback_message: Optional[str] = None


async def find_initial_model(
    services: Any,
    *,
    preferred_model: Optional[Model] = None,
    cli_provider: Optional[str] = None,
    cli_model: Optional[str] = None,
    scoped_models: Optional[List[ScopedModelConfig]] = None,
    is_continuing: bool = False,
    default_provider: Optional[str] = None,
    default_model_id: Optional[str] = None,
    default_thinking_level: Optional[ModelThinkingLevel] = None,
    agent_model: Optional[str] = None,
) -> InitialModelResult:
    """按优先级确定初始模型。

    优先级：
    1. 调用方显式传入的 ``preferred_model``
    2. CLI 参数（``--provider`` / ``--model``）
    3. 作用域模型列表中的第一个（仅当非继续会话时）
    4. agent 组合声明 yaml 的 ``model:`` 字段（``agent_model``，人格默认
       模型——如 scout 类轻量代理可声明便宜模型；解析失败或无鉴权时
       静默落回后续层级）
    5. settings 中的默认模型
    6. 任意一个有鉴权的可用模型（优先 ``DEFAULT_MODEL_PER_PROVIDER``）
    """
    if preferred_model is not None:
        return InitialModelResult(model=preferred_model)

    registry = services.model_runtime
    scoped_models = scoped_models or []

    if cli_model:
        resolved = resolve_cli_model(
            cli_provider=cli_provider,
            cli_model=cli_model,
            model_runtime=registry,
        )
        if resolved.error:
            return InitialModelResult(
                model=None,
                fallback_message=resolved.error,
            )
        if resolved.model:
            return InitialModelResult(
                model=resolved.model,
                thinking_level=resolved.thinking_level,
                fallback_message=resolved.warning,
            )

    if scoped_models and not is_continuing:
        first = scoped_models[0]
        return InitialModelResult(
            model=first.model,
            thinking_level=first.thinking_level or default_thinking_level,
        )

    # agent 组合声明的人格默认模型（tier 4）：字符串形如 "provider/model"，
    # 复用 CLI 解析（支持模糊匹配与自定义模型 id——警告随 fallback_message
    # 透出，与 CLI 路径一致）；解析失败（如未知 provider）或无鉴权静默落回
    # 后续层级。
    if agent_model:
        resolved_agent = resolve_cli_model(
            cli_provider=None,
            cli_model=agent_model,
            model_runtime=registry,
        )
        if resolved_agent.model is not None and await registry.get_api_key(
            resolved_agent.model
        ):
            return InitialModelResult(
                model=resolved_agent.model,
                thinking_level=resolved_agent.thinking_level or default_thinking_level,
                fallback_message=resolved_agent.warning,
            )

    if default_provider and default_model_id:
        found = registry.find(default_provider, default_model_id)
        if found is not None and await registry.get_api_key(found):
            return InitialModelResult(
                model=found,
                thinking_level=default_thinking_level,
            )

    available_models = registry.get_available_snapshot()
    if available_models:
        for provider, default_id in DEFAULT_MODEL_PER_PROVIDER.items():
            match = next(
                (
                    m
                    for m in available_models
                    if m.provider == provider and m.id == default_id
                ),
                None,
            )
            if match is not None:
                return InitialModelResult(model=match)
        return InitialModelResult(model=available_models[0])

    return InitialModelResult(
        model=None,
        fallback_message="No models are available. Please configure an API key.",
    )


async def restore_model_from_session(
    *,
    saved_provider: str,
    saved_model_id: str,
    current_model: Optional[Model],
    model_runtime: Any,
) -> "RestoreModelResult":
    """从会话记录恢复模型；失败时回退到当前模型或第一个可用模型。"""
    restored = model_runtime.find(saved_provider, saved_model_id)
    has_auth = False
    if restored is not None:
        if hasattr(model_runtime, "has_configured_auth"):
            has_auth = model_runtime.has_configured_auth(restored)
        else:
            key = await model_runtime.get_api_key(restored)
            has_auth = key is not None

    if restored is not None and has_auth:
        return RestoreModelResult(model=restored)

    reason = "model no longer exists" if restored is None else "no auth configured"
    fallback_message = (
        f"Could not restore model {saved_provider}/{saved_model_id} ({reason})."
    )

    if current_model is not None:
        return RestoreModelResult(
            model=current_model,
            fallback_message=f"{fallback_message} Using {_provider_str(current_model.provider)}/{current_model.id}.",
        )

    available_models = model_runtime.get_available_snapshot()
    if available_models:
        for provider, default_id in DEFAULT_MODEL_PER_PROVIDER.items():
            match = next(
                (
                    m
                    for m in available_models
                    if m.provider == provider and m.id == default_id
                ),
                None,
            )
            if match is not None:
                return RestoreModelResult(
                    model=match,
                    fallback_message=f"{fallback_message} Using {_provider_str(match.provider)}/{match.id}.",
                )
        fallback = available_models[0]
        return RestoreModelResult(
            model=fallback,
            fallback_message=f"{fallback_message} Using {_provider_str(fallback.provider)}/{fallback.id}.",
        )

    return RestoreModelResult(model=None, fallback_message=None)


@dataclass(frozen=True)
class RestoreModelResult:
    """会话模型恢复结果。"""

    model: Optional[Model]
    fallback_message: Optional[str] = None


# ---------------------------------------------------------------------------
# Thinking level
# ---------------------------------------------------------------------------


def resolve_thinking_level(
    services: Any,
    session_manager: Any,
    model: Optional[Model],
    preferred_level: Optional[ModelThinkingLevel] = None,
) -> Optional[ModelThinkingLevel]:
    """确定初始思考级别，优先恢复会话上下文中的级别。

    - 无 model 或无法确定级别时返回 None（调用方按 off 处理）。
    - 按模型能力（thinking_level_map）吸附到最近的支持级别。
    """
    if preferred_level is not None:
        level = preferred_level
    else:
        session_context = session_manager.build_session_context()
        has_thinking_entry = any(
            e.type == "thinking_level_change" for e in session_manager.get_branch()
        )
        default_level = (
            services.settings_manager.get_default_thinking_level()
            or DEFAULT_THINKING_LEVEL
        )
        if has_thinking_entry:
            level = session_context.thinking_level
        else:
            level = default_level

    if model is None or level is None:
        return None

    return clamp_thinking_level(
        model, ModelThinkingLevel(getattr(level, "value", level))
    )


__all__ = [
    "DEFAULT_MODEL_PER_PROVIDER",
    "InitialModelResult",
    "ParsedModelResult",
    "RestoreModelResult",
    "ResolveCliModelResult",
    "find_exact_model_reference_match",
    "find_initial_model",
    "parse_model_pattern",
    "resolve_cli_model",
    "resolve_model_scope",
    "resolve_thinking_level",
    "restore_model_from_session",
]
