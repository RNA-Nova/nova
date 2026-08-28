# auth/guidance.py
"""鉴权失败时的用户引导文案（对齐 TS ``core/auth-guidance.ts``）。

集中维护"No model selected / No API key / OAuth 过期"三类错误的提示，
统一带上重新登录指引，避免各调用点各自拼裸错误信息。
"""

UNKNOWN_PROVIDER = "unknown"


def get_provider_login_help() -> str:
    """登录指引（TS 版附带 docs 路径，Python 侧无 docs 站点，只保留命令指引）。"""
    return "Use /login to log into a provider via OAuth or API key."


def format_no_models_available_message() -> str:
    return f"No models available. {get_provider_login_help()}"


def format_no_model_selected_message() -> str:
    return (
        f"No model selected.\n\n{get_provider_login_help()}\n\n"
        "Then use /model to select a model."
    )


def format_no_api_key_found_message(provider: str) -> str:
    provider_display = (
        "the selected model" if provider == UNKNOWN_PROVIDER else provider
    )
    return f"No API key found for {provider_display}.\n\n{get_provider_login_help()}"


def format_oauth_reauth_message(provider: str) -> str:
    """OAuth 凭证失效的专属提示（对齐 TS agent-session 的 OAuth 分支）。"""
    return (
        f'Authentication failed for "{provider}". '
        "Credentials may have expired or network is unavailable. "
        f"Run '/login {provider}' to re-authenticate."
    )


def format_no_auth_message(provider: str, is_oauth: bool) -> str:
    """无可用鉴权的统一文案：OAuth provider 给重登指引，其余给 /login 指引。"""
    if is_oauth:
        return format_oauth_reauth_message(provider)
    return format_no_api_key_found_message(provider)


__all__ = [
    "UNKNOWN_PROVIDER",
    "format_no_api_key_found_message",
    "format_no_model_selected_message",
    "format_no_models_available_message",
    "format_oauth_reauth_message",
    "format_no_auth_message",
    "get_provider_login_help",
]
