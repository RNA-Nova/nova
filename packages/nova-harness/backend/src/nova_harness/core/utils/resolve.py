"""请求级 API key 解析辅助。"""

from nova_harness.core.config.auth.guidance import format_no_model_selected_message


async def resolve_api_key(provider, model_runtime):
    """根据 provider 解析对应的 API key。

    使用请求中的 provider（agent state 里的 model 可能在对话中途已被切换）。
    """
    resolved_provider = provider

    if not resolved_provider:
        raise Exception(format_no_model_selected_message())

    return await model_runtime.get_api_key_for_provider(resolved_provider)
