"""ModelsManager 目录站路由测试（``_catalog_service_for`` + partial 绑定发放）。

四种形态：容器缺席 / 单站全员共用 / dict 按 ``catalog_source`` 路由 /
dict 未命中（含未选配）。发放物是站经 ``partial(service.fetch, provider.id)``
绑定后的 ``(signal, validator) -> CatalogOutcome``。
"""

from typing import List, Optional

import pytest

from nova_ai.gateway.catalog import CatalogOutcome
from nova_ai.gateway.factory import create_provider
from nova_ai.gateway.models import create_models_manager
from nova_ai.gateway.provider import RefreshModelsContext

pytestmark = pytest.mark.asyncio


class _FakeStation:
    """假目录站（``CatalogService`` 形态：记录调用，回固定 outcome）。"""

    def __init__(self, status: int = 200) -> None:
        self.calls: List[dict] = []
        self._status = status

    async def fetch(self, key, signal, validator) -> CatalogOutcome:
        self.calls.append({"key": key, "validator": validator})
        return CatalogOutcome(status=self._status)


def _dynamic_provider(
    provider_id: str,
    captured: List[RefreshModelsContext],
    catalog_source: Optional[str] = None,
):
    """只捕获 context 的动态 provider（refresh_models 裸口袋直赋——
    路由发放发生在 manager 的 ``_run_provider_refresh_phase``，与策略无关）。"""

    async def _refresh(context: RefreshModelsContext) -> None:
        captured.append(context)

    provider = create_provider(
        id=provider_id,
        name=provider_id,
        catalog_source=catalog_source,
    )
    provider.refresh_models = _refresh
    return provider


class TestCatalogServiceRouting:
    async def test_no_container_grants_no_fetch(self):
        """无站容器 → ctx.catalog_fetch 不发放。"""
        captured: List[RefreshModelsContext] = []
        provider = _dynamic_provider("p1", captured, catalog_source="pi.dev")
        manager = create_models_manager()
        manager.set_provider(provider)
        await manager.refresh(allow_network=True)
        assert captured[0].catalog_fetch is None

    async def test_single_station_shared_regardless_of_source(self):
        """单站形态：全员共用（provider 未选配也发放），key 绑定 provider id。"""
        station = _FakeStation()
        captured: List[RefreshModelsContext] = []
        provider = _dynamic_provider("p1", captured)  # catalog_source=None
        manager = create_models_manager(catalog_services=station)
        manager.set_provider(provider)
        await manager.refresh(allow_network=True)

        fetch = captured[0].catalog_fetch
        assert fetch is not None

        outcome = await fetch(None, '"v1"')
        assert outcome.status == 200
        assert station.calls == [{"key": "p1", "validator": '"v1"'}]

    async def test_dict_routes_by_catalog_source(self):
        """dict 形态：按 provider.catalog_source 路由到对应站。"""
        station_a = _FakeStation()
        station_b = _FakeStation()
        captured: List[RefreshModelsContext] = []
        provider = _dynamic_provider("p1", captured, catalog_source="a")
        manager = create_models_manager(
            catalog_services={"a": station_a, "b": station_b}
        )
        manager.set_provider(provider)
        await manager.refresh(allow_network=True)

        fetch = captured[0].catalog_fetch
        assert fetch is not None

        await fetch(None, None)
        assert len(station_a.calls) == 1
        assert station_b.calls == []

    async def test_dict_unknown_key_grants_no_fetch(self):
        """dict 形态：选配的 key 在容器中查不到 → 不发放。"""
        captured: List[RefreshModelsContext] = []
        provider = _dynamic_provider("p1", captured, catalog_source="missing")
        manager = create_models_manager(catalog_services={"a": _FakeStation()})
        manager.set_provider(provider)
        await manager.refresh(allow_network=True)
        assert captured[0].catalog_fetch is None

    async def test_dict_unselected_provider_grants_no_fetch(self):
        """dict 形态：provider 未选配站（catalog_source=None）→ 不发放。"""
        captured: List[RefreshModelsContext] = []
        provider = _dynamic_provider("p1", captured)
        manager = create_models_manager(catalog_services={"a": _FakeStation()})
        manager.set_provider(provider)
        await manager.refresh(allow_network=True)
        assert captured[0].catalog_fetch is None

    async def test_station_outcome_reaches_callback(self):
        """站的拉取结果原样回到 refresh 实现（ outcome 直通不加工）。"""
        station = _FakeStation(status=503)
        captured: List[RefreshModelsContext] = []
        provider = _dynamic_provider("p1", captured, catalog_source="a")
        manager = create_models_manager(catalog_services={"a": station})
        manager.set_provider(provider)
        await manager.refresh(allow_network=True)

        fetch = captured[0].catalog_fetch
        assert fetch is not None

        outcome = await fetch(None, None)
        assert outcome.status == 503
