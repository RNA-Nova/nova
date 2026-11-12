"""Moonshot AI CN：与全球站共享构建，仅 source/base_url 不同。"""

from ..define import define_provider
from .moonshotai import MOONSHOTAI_COMPAT, moonshot_models

BASE_URL = "https://api.moonshot.cn/v1"

DEF = define_provider(
    id="moonshotai-cn",
    models=lambda ctx: moonshot_models(ctx, "moonshotai-cn", BASE_URL),
    compat={"openai-completions": MOONSHOTAI_COMPAT},
)
