async def resolve_api_key(provider, model_registry):
    """
    根据 provider 解析对应的 API key。

    Args:
        provider: 从请求中传入的 provider 参数。
        agent_state: 包含当前模型信息（model）的 agent 状态对象。
        model_registry: 用于获取 API key。

    Returns:
        解析得到的 API key 字符串。
    """
    # 使用请求中的 provider；agent_state.model 可能在对话中途已被切换
    resolved_provider = provider

    if not resolved_provider:
        raise Exception("No model selected")

    key = await model_registry.get_api_key_for_provider(resolved_provider)

    return key
