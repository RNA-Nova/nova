"""
基础模型类型定义
包含模型定义、模型成本、用量统计
"""

from dataclasses import dataclass
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict, Union

from pydantic import AliasChoices, BeforeValidator, Field, model_validator

from .base_model import NovaBaseModel
from .compat import (
    AnthropicMessagesCompat,
    OpenAICompletionsCompat,
    OpenAIResponsesCompat,
    SystemOneCompat,
)
from .cost import ModelCost, ModelCostTier
from .enums import Api, ClassifierApi, KnownApi, ProviderId, ThinkingLevelMap

_COMPAT_CLASS_BY_API = {
    KnownApi.OPENAI_COMPLETIONS.value: OpenAICompletionsCompat,
    KnownApi.OPENAI_RESPONSES.value: OpenAIResponsesCompat,
    KnownApi.AZURE_OPENAI_RESPONSES.value: OpenAIResponsesCompat,
    KnownApi.OPENAI_CODEX_RESPONSES.value: OpenAIResponsesCompat,
    KnownApi.ANTHROPIC_MESSAGES.value: AnthropicMessagesCompat,
}
"""api → compat 类的显式判别映射（与 ``Model._set_default_compat`` 的默认选择一致）。"""


class ModelCostTierFields(TypedDict, total=False):
    """目录构建期的分层费率字段包；``inputTokensAbove`` 沿用目录原始键，
    值为整数 token 的严格大于阈值，在 ``ModelCostTier`` 边界归一为
    ``input_tokens_above``。"""

    inputTokensAbove: int
    input: float
    output: float
    cache_read: float
    cache_write: float


class ModelCostFields(TypedDict, total=False):
    """模型目录字段包的 cost 形状（规则 10：声明不校验——``ModelCost``
    构造时验收）。"""

    input: float
    output: float
    cache_read: float
    cache_write: float
    tiers: List[ModelCostTierFields]


class ModelFields(TypedDict, total=False):
    """chat/image/classifier 目录字段包（规则 10：声明不校验）。

    跨构建管线与运行期的共享词汇；目录边界按 ``type`` 选择
    ``Model`` / ``ImageModel`` / ``ClassifierModel`` 一次性校验。
    """

    id: str
    name: str
    reasoning: bool
    input_types: List[str]
    cost: ModelCostFields
    context_window: int
    max_tokens: int
    thinking_level_map: ThinkingLevelMap
    headers: Dict[str, str]
    base_url: str
    api: str
    provider: str
    # 目录条目用途；chat 缺省用于兼容旧数据，image/classifier 必须显式标注。
    type: str
    # 图像输出模态（pi ``ImageModel.output``）；chat/classifier 不使用。
    output_types: List[str]
    # compat patch——合并进 compat 类的开放子集（键集合由
    # ``OpenAICompletionsCompat``/``OpenAIResponsesCompat``/``AnthropicMessagesCompat``
    # 及分类的 ``SystemOneCompat`` 各自定义）
    compat: Dict[str, Any]
    # 输入限制（pi ``Model.inputLimits``——pi.dev 已携）
    input_limits: Dict[str, Any]
    # prompt cache 各保留档寿命秒（pi ``Model.promptCache``）
    prompt_cache: Dict[str, int]
    sampling_params: Dict[str, Any]
    sampling_params_by_thinking_level: Dict[str, Dict[str, Any]]


@dataclass(frozen=True)
class MaterializedCatalog:
    """构建/运行共享的原料物化结果：字段包 + 应淘汰的模型 ID。

    这是内部值对象，不直接序列化；显式删除集防止 overlay 的“缺席”
    被误当成“从基线删除”。运行期 personalized 策略在 merge 后消费。
    """

    models_fields: List[ModelFields]
    remove_ids: frozenset[str] = frozenset()


class ModelImageResizeOptions(NovaBaseModel):
    """图像输入的缓存安全缩放配置；``None`` = 该维度不设本地限制。"""

    max_width: Optional[int] = Field(default=None, gt=0)
    max_height: Optional[int] = Field(default=None, gt=0)
    # base64 编码后的载荷上限（字节）；None = 不设上限
    max_bytes: Optional[int] = Field(default=None, gt=0)
    # None = 重编码时沿用实现默认质量
    jpeg_quality: Optional[int] = Field(default=None, gt=0, le=100)


class ModelImageInputLimits(NovaBaseModel):
    """图像输入限制；``None`` = 对应限制未知，消费方不主动截断。"""

    # 新图像进入会话历史前应用的缩放配置；None = 无已知缩放策略
    resize: Optional[ModelImageResizeOptions] = None
    # 单条 provider 消息接受的图像上限；None = 未知
    max_per_message: Optional[int] = Field(default=None, gt=0)
    # 单次 provider 请求跨消息接受的图像上限；None = 未知
    max_per_request: Optional[int] = Field(default=None, gt=0)


class ModelInputLimits(NovaBaseModel):
    """provider 输入限制与预处理元数据；``None`` = 无已知限制。"""

    # 序列化后的 provider 请求体上限（字节）；None = 未知
    max_request_bytes: Optional[int] = Field(default=None, gt=0)
    images: Optional[ModelImageInputLimits] = None


class ModelPromptCache(NovaBaseModel):
    """prompt cache 各保留档的寿命（秒）；``None`` = 寿命未知，不预热该档。"""

    short: Optional[int] = Field(default=None, gt=0)
    long: Optional[int] = Field(default=None, gt=0)


class ModelBase(NovaBaseModel):
    """聊天、图像与分类模型共享的目录字段；具体操作由 ``type`` 判别。"""

    id: str
    name: str
    api: Api
    provider: ProviderId
    base_url: str
    input_types: List[Literal["text", "image"]]
    cost: ModelCost
    headers: Optional[Dict[str, str]] = None
    # provider 输入限制与缓存安全预处理元数据；``None`` = 无已知限制。
    input_limits: Optional[ModelInputLimits] = None


class Model(ModelBase):
    """聊天模型定义；缺省 ``type`` 为 chat，兼容旧目录与存储。

    - ``headers``：静态默认请求头；``None`` = 无；
    - ``compat``：显式兼容性配置（逐字段覆盖自动检测）；``None`` = 全自动检测；
    - ``sampling_params``：模型级默认采样参数（对齐 pi ``Model.samplingParams``），
      合并进请求体顶层，请求级 ``StreamOptions.sampling_params`` 按键覆盖；
      ``None`` = 无默认。
    """

    type: Literal["chat"] = "chat"
    reasoning: bool
    thinking_level_map: Optional[ThinkingLevelMap] = None
    context_window: int
    max_tokens: int
    compat: Optional[
        Union[OpenAICompletionsCompat, OpenAIResponsesCompat, AnthropicMessagesCompat]
    ] = None
    sampling_params: Optional[Dict[str, Any]] = None
    # 按生效思考级别覆盖的采样参数；级别参数高于模型默认、低于请求覆盖。
    sampling_params_by_thinking_level: Optional[Dict[str, Dict[str, Any]]] = None
    # prompt cache 各保留档寿命（对齐 pi ``Model.promptCache``）；``None`` =
    # 缓存行为未知
    prompt_cache: Optional[ModelPromptCache] = None

    @model_validator(mode="before")
    @classmethod
    def _resolve_compat_class(cls, data):
        """按 api 显式判别 compat 的 union 成员，不依赖 smart-union 猜测。

        三个 compat 类的字段全部 Optional 且部分同名，裸 dict 走 smart-union
        可能判错成员（如 ``{"supports_developer_role": true}`` 同时命中两类）。
        判别依据与 ``_set_default_compat`` 一致：以 api 为准。
        未知 api 的 compat 保持原样，交给 pydantic 默认处理。
        """
        if isinstance(data, dict):
            compat = data.get("compat")
            api = data.get("api")
            api_value = getattr(api, "value", api)
            # 非字符串的 api（异常输入）不做判别——交 pydantic 默认处理
            compat_cls = (
                _COMPAT_CLASS_BY_API.get(api_value)
                if isinstance(api_value, str)
                else None
            )
            if isinstance(compat, dict) and compat_cls is not None:
                data = dict(data)
                data["compat"] = compat_cls.model_validate(compat)
            elif (
                compat is not None
                and compat_cls is not None
                and not isinstance(compat, compat_cls)
            ):
                # 实例输入也不能绕过 API 判别；拒绝错配，不隐式转换或剥字段。
                raise ValueError(
                    f"compat instance {type(compat).__name__} does not match api {api_value}"
                )
        return data

    @model_validator(mode="after")
    def _set_default_compat(self):
        """按 API 共用显式判别表补默认 compat。"""
        if self.compat is None:
            api = getattr(self.api, "value", self.api)
            compat_cls = _COMPAT_CLASS_BY_API.get(api)
            if compat_cls is not None:
                self.compat = compat_cls()
        return self


class ImageModel(ModelBase):
    """图像生成模型；只能经图像调用面使用，不能进入聊天流。"""

    type: Literal["image"]
    # 输出模态（pi ``ImageModel.output``）；text 表示可同时返回文本块。
    output_types: List[Literal["text", "image"]]

    @model_validator(mode="after")
    def _requires_image_output(self):
        if "image" not in self.output_types:
            raise ValueError("ImageModel output_types must include image")
        return self


class ClassifierModel(ModelBase):
    """结构化分类模型；只能经 ``classify()`` 调用面使用。

    ``compat`` 为 None = 按 provider、base_url 与模型 ID 自动检测 System One
    图像输入形式；显式字段逐键覆盖自动检测。
    """

    type: Literal["classifier"]
    api: ClassifierApi
    context_window: int
    compat: Optional[SystemOneCompat] = None


ModelType = Literal["chat", "image", "classifier"]
"""目录条目支持的模型操作类型。"""


def _default_any_model_type(value: Any) -> Any:
    """旧目录缺省 type 在联合入口补 chat，再交给判别联合。"""
    if isinstance(value, dict) and "type" not in value:
        return {**value, "type": "chat"}
    return value


AnyModel = Annotated[
    Union[Model, ImageModel, ClassifierModel],
    Field(discriminator="type"),
    BeforeValidator(_default_any_model_type),
]
"""可持久化混合目录；旧 chat 条目允许缺省 type。"""


class Cost(NovaBaseModel):
    """成本明细"""

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0
    # True = 总额来自 provider 账单字段；False/None = 按目录费率估算。
    total_from_provider: Optional[bool] = None


class Usage(NovaBaseModel):
    """令牌使用统计。

    - ``cache_write_1h``：Anthropic 1h 缓存写入量（``cache_write`` 的子集）；
      ``None`` = provider 未区分；
    - ``reasoning``：reasoning/thinking tokens（``output`` 的子集）；
      ``None`` = provider 未报告。
    """

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    # to_camel 会把末尾 1h 错写成 1H；显式对齐 pi 的 cacheWrite1h。
    cache_write_1h: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices("cacheWrite1h", "cache_write_1h"),
        serialization_alias="cacheWrite1h",
    )
    reasoning: Optional[int] = None
    total_tokens: int = 0
    cost: Cost = Field(default_factory=Cost)


class ModelsStoreEntry(NovaBaseModel):
    """单个 provider 的动态模型目录条目（``models-store.json`` 的落盘 schema）。

    跨组件序列化词汇：schema 归枢纽，存储实现归消费方
    （nova_ai 内存实现 / nova_harness 文件实现）。未知操作类型逐条丢弃，
    已知类型的畸形条目仍响亮失败，避免未来 minor 成员毒化整份旧缓存。

    - ``checked_at``：上次成功校验的 Unix 毫秒时间戳；``None`` = 未知
      （新鲜度窗口不生效，下次刷新走网络）；
    - ``last_modified``：远程目录 Last-Modified 的 Unix 毫秒时间戳，
      与基线 ``generatedAt`` 做新鲜度竞速——早于基线的 overlay 被忽略；
      ``None`` = 无条件请求锚点；
    - ``etag``：远程目录 ETag 原样存储（含引号），条件请求回传
      If-None-Match；``None`` = 无。
    """

    models: List[AnyModel]
    checked_at: Optional[int] = None
    last_modified: Optional[int] = None
    etag: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _drop_unknown_model_types(cls, data):
        if not isinstance(data, dict) or not isinstance(data.get("models"), list):
            return data
        models = []
        for model in data["models"]:
            if isinstance(model, dict) and model.get("type", "chat") not in (
                "chat",
                "image",
                "classifier",
            ):
                continue
            models.append(model)
        return {**data, "models": models}
