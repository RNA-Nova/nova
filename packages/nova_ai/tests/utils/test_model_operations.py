"""模型操作公共出口的错误脱敏测试。"""

from nova_ai.utils.model_operations import image_error_result
from nova_protocol import ImageModel, ModelCost


def test_image_error_result_redacts_provider_error_text():
    model = ImageModel(
        id="image",
        name="Image",
        type="image",
        api="openrouter-images",
        provider="openrouter",
        base_url="https://example.com/v1",
        input_types=["text"],
        output_types=["image"],
        cost=ModelCost(),
    )
    result = image_error_result(
        model, RuntimeError("Bearer sk-live-FAKE-REVIEW-SECRET")
    )
    assert "sk-live-FAKE-REVIEW-SECRET" not in (result.error_message or "")
