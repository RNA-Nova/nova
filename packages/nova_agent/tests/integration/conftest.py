"""真实模型探针装配：每测试一个新 manager，避免跨用例凭据与事件循环状态。"""

import pytest
import pytest_asyncio
from integration._helpers.auth import build_manager, has_credential


@pytest_asyncio.fixture
async def runtime():
    if not has_credential():
        pytest.skip("需要 VOLCENGINE_API_KEY 或 auth.json 中的 volcengine 凭据")
    manager = await build_manager()
    models = manager.get_models("volcengine")
    if not models:
        pytest.skip("volcengine 目录中没有可用聊天模型")
    model = min(models, key=lambda item: (item.cost.input + item.cost.output, item.id))
    return manager, model
