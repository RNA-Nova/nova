"""Moonshot AI CN：与全球站共享定义和源映射，仅 id/base_url 不同。"""

from nova_protocol import KnownApi

from ..define import define_provider
from .moonshotai import (
    MOONSHOTAI_COMPAT_DEFAULTS,
    MOONSHOTAI_MODEL_CORRECTIONS,
    map_moonshot_model,
)

DEF = define_provider(
    id="moonshotai-cn",
    name="Moonshot AI CN",
    base_url="https://api.moonshot.cn/v1",
    api=KnownApi.OPENAI_COMPLETIONS,
    compat_defaults=MOONSHOTAI_COMPAT_DEFAULTS,
    model_corrections=MOONSHOTAI_MODEL_CORRECTIONS,
    map_model=map_moonshot_model,
)
