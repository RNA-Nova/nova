"""请求身份与 provider 归因词汇（可持久化纯 JSON）。"""

from typing import Annotated, List, Literal, Optional, Union

from pydantic import ConfigDict, Field

from .base_model import NovaBaseModel


class UserAgentCustom(NovaBaseModel):
    """自定义完整 User-Agent。"""

    model_config = ConfigDict(frozen=True)
    custom: str


UserAgentMode = Annotated[
    Union[Literal["short", "detailed"], UserAgentCustom],
    Field(union_mode="left_to_right"),
]
"""short=产品短名；detailed=版本与平台；对象=完整自定义值。"""


class ClientAttribution(NovaBaseModel):
    """provider 排名/归因所需的应用身份。"""

    model_config = ConfigDict(frozen=True)
    name: str
    url: str
    categories: Optional[List[str]] = None


class ClientIdentity(NovaBaseModel):
    """请求身份；缺席字段在 merge 时表示不覆盖，显式 null 可关闭归因。"""

    model_config = ConfigDict(frozen=True)
    user_agent: Optional[UserAgentMode] = None
    attribution: Optional[ClientAttribution] = None
    version: Optional[str] = None


__all__ = [
    "ClientAttribution",
    "ClientIdentity",
    "UserAgentCustom",
    "UserAgentMode",
]
