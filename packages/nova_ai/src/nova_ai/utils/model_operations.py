"""模型操作判别及非流式错误结果（pi ``utils/model-operations.ts``）。"""

import time

from nova_protocol import (
    AnyModel,
    Api,
    AssistantImages,
    ClassifierApi,
    ClassifierContext,
    ClassifierModel,
    ClassifierResult,
    ClassifierStopReason,
    ImagesStopReason,
    KnownApi,
    KnownClassifierApi,
    ModelType,
    redact_secrets,
)


def api_value(api: Api | ClassifierApi) -> str:
    """开放 API 字符串或已知 API 枚举统一归一成注册表键。"""
    return api.value if isinstance(api, (KnownApi, KnownClassifierApi)) else str(api)


def get_model_type(model: AnyModel) -> ModelType:
    """旧 chat 对象缺 type 时也按聊天模型处理。"""
    return model.type


def assert_classifier_model(model: AnyModel) -> ClassifierModel:
    """运行时收窄分类模型；其他操作类型不得进入分类调用。"""
    if not isinstance(model, ClassifierModel):
        raise TypeError(f"Model {model.provider}/{model.id} is not a classifier model")
    return model


def assert_classifier_input_supported(
    model: ClassifierModel, context: ClassifierContext
) -> None:
    """文本分类模型不得接收图片；失败发生在任何网络调用之前。"""
    if context.images and "image" not in model.input_types:
        raise TypeError(
            f"Model {model.provider}/{model.id} does not accept image input"
        )


def image_error_result(
    model: AnyModel, error: Exception, *, aborted: bool = False
) -> AssistantImages:
    """图像调用失败的结果；不抛给上层，取消与故障保持不同终态。"""
    return AssistantImages(
        api=model.api,
        provider=model.provider,
        model=model.id,
        output=[],
        stop_reason=ImagesStopReason.ABORTED if aborted else ImagesStopReason.ERROR,
        error_message=redact_secrets(str(error)),
        timestamp=int(time.time() * 1000),
    )


def classifier_error_result(
    model: AnyModel, error: Exception, *, aborted: bool = False
) -> ClassifierResult:
    """分类调用失败的结果；取消与故障保持不同终态。"""
    return ClassifierResult(
        api=model.api,
        provider=model.provider,
        model=model.id,
        answers={},
        stop_reason=(
            ClassifierStopReason.ABORTED if aborted else ClassifierStopReason.ERROR
        ),
        error_message=redact_secrets(str(error)),
        timestamp=int(time.time() * 1000),
    )


__all__ = [
    "api_value",
    "assert_classifier_input_supported",
    "assert_classifier_model",
    "classifier_error_result",
    "get_model_type",
    "image_error_result",
]
