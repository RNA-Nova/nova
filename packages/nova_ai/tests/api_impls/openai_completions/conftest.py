"""openai_completions 测试共享模型与上下文 builder。"""

from nova_protocol import Context, KnownApi, KnownProvider, Message, Model, ModelCost


def _model(**overrides) -> Model:
    model = Model(
        id="test-model",
        name="Test",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.OPENAI,
        base_url="https://api.openai.com",
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
        context_window=128000,
        max_tokens=4096,
    )
    return model.model_copy(update=overrides)


def _ctx(*messages: Message) -> Context:
    return Context(messages=list(messages))
