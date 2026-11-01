"""Moonshot AI CN 的构建期知识（与 moonshotai 同构，仅 id/base_url 不同）。

目录历史上派生自 moonshotai——定义件直接共享（别名同源，防两处漂移）。
"""

from nova_protocol import KnownApi

from ..define import define_provider
from .moonshotai import MOONSHOTAI_COMPAT_DEFAULTS, MOONSHOTAI_MODEL_CORRECTIONS

DEF = define_provider(
    id="moonshotai-cn",
    name="Moonshot AI CN",
    base_url="https://api.moonshot.cn/v1",
    api=KnownApi.OPENAI_COMPLETIONS,
    compat_defaults=MOONSHOTAI_COMPAT_DEFAULTS,
    model_corrections=MOONSHOTAI_MODEL_CORRECTIONS,
)
