"""结构化分类调用词汇（对位 pi ``Classifier*`` 家族）。

分类是一次非流式的结构化决策：输入状态与一组问题，输出逐问题的
choice/score/bool 答案；不复用聊天消息或图像生成结果。
"""

from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import Field

from .base_model import NovaBaseModel
from .content import ImageContent
from .enums import ClassifierApi, ClassifierStopReason, ProviderId
from .model import Usage


class ClassifierChoiceQuestion(NovaBaseModel):
    """从命名候选中选择一个答案；criteria 的键即候选标签。"""

    type: Literal["choice"] = "choice"
    instructions: str
    criteria: Dict[str, str]


class ClassifierScoreQuestion(NovaBaseModel):
    """按有序评分标准给出分数；criteria 的顺序定义评分标尺。"""

    type: Literal["score"] = "score"
    instructions: str
    criteria: List[str]


class ClassifierBoolCriteria(NovaBaseModel):
    """布尔问题的真假两侧说明。"""

    true: str
    false: str


class ClassifierBoolQuestion(NovaBaseModel):
    """判断命题是否成立；线上 System One 协议映射为 ``noul``。"""

    type: Literal["bool"] = "bool"
    instructions: str
    criteria: ClassifierBoolCriteria


ClassifierQuestion = Annotated[
    Union[ClassifierChoiceQuestion, ClassifierScoreQuestion, ClassifierBoolQuestion],
    Field(discriminator="type"),
]
"""分类问题的 ``type`` 判别联合。"""


class ClassifierContext(NovaBaseModel):
    """一次分类的输入状态、可选图片与逐 ID 问题集。"""

    # 任意 JSON 状态（自由负载）：字符串或结构化对象均由 provider 解释。
    state: Any
    # None = 无图片；只有 input_types 含 image 的模型接受。
    images: Optional[List[ImageContent]] = None
    questions: Dict[str, ClassifierQuestion]


class ClassifierChoiceAnswer(NovaBaseModel):
    """选择答案及其候选概率与置信度。"""

    type: Literal["choice"] = "choice"
    choice: str
    probabilities: Dict[str, float]
    confidence: float


class ClassifierScoreAnswer(NovaBaseModel):
    """评分答案及置信度。"""

    type: Literal["score"] = "score"
    score: float
    confidence: float


class ClassifierBoolAnswer(NovaBaseModel):
    """布尔答案；probability 是命题为真的 0–1 概率。"""

    type: Literal["bool"] = "bool"
    probability: float


ClassifierAnswer = Annotated[
    Union[ClassifierChoiceAnswer, ClassifierScoreAnswer, ClassifierBoolAnswer],
    Field(discriminator="type"),
]
"""分类答案的 ``type`` 判别联合。"""


class ClassifierResult(NovaBaseModel):
    """一次分类的终态结果；错误与取消不通过异常冒泡。

    ``usage`` 为 None = provider 未报告 token 用量；``error_message``
    仅在 error/aborted 终态携带脱敏后的诊断文本。
    """

    api: ClassifierApi
    provider: ProviderId
    model: str
    answers: Dict[str, ClassifierAnswer] = Field(default_factory=dict)
    usage: Optional[Usage] = None
    stop_reason: ClassifierStopReason = ClassifierStopReason.STOP
    error_message: Optional[str] = None
    timestamp: int = 0


__all__ = [
    "ClassifierAnswer",
    "ClassifierBoolAnswer",
    "ClassifierBoolCriteria",
    "ClassifierBoolQuestion",
    "ClassifierChoiceAnswer",
    "ClassifierChoiceQuestion",
    "ClassifierContext",
    "ClassifierQuestion",
    "ClassifierResult",
    "ClassifierScoreAnswer",
    "ClassifierScoreQuestion",
]
