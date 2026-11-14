"""
utils.resolve_api_key 测试。
"""

import pytest

from nova_harness.core.utils import resolve_api_key


@pytest.mark.asyncio
async def test_resolve_api_key_delegates_to_registry():
    class Registry:
        async def get_api_key_for_provider(self, provider):
            return f"key-for-{provider}"

    assert await resolve_api_key("volcengine", Registry()) == "key-for-volcengine"


@pytest.mark.asyncio
async def test_resolve_api_key_no_provider_raises():
    class Registry:
        async def get_api_key_for_provider(self, provider):
            return None

    with pytest.raises(Exception, match="No model selected"):
        await resolve_api_key("", Registry())
