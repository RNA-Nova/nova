"""gateway 图像测试共享的合法模型与公开图像实现接缝。"""

from nova_protocol import (
    AssistantImages,
    ImageModel,
    ImagesStopReason,
    Model,
    ModelCost,
    TextContent,
)


def _chat(model_id="shared"):
    return Model(
        id=model_id,
        name="chat",
        api="openai-completions",
        provider="test",
        base_url="https://example.com/v1",
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(),
        context_window=1000,
        max_tokens=100,
    )


def _image(model_id="shared", provider="test"):
    return ImageModel(
        id=model_id,
        name="image",
        type="image",
        api="openrouter-images",
        provider=provider,
        base_url="https://example.com/v1",
        input_types=["text"],
        output_types=["image"],
        cost=ModelCost(),
    )


class FakeImages:
    def __init__(self):
        self.calls = []

    async def generate_images(self, model, context, options=None):
        self.calls.append((model, context, options))
        return AssistantImages(
            api=model.api,
            provider=model.provider,
            model=model.id,
            output=[TextContent(text="ok")],
            stop_reason=ImagesStopReason.STOP,
        )
