"""utils/http.py 薄封装原语测试（重试语义 / abort 在飞中断 / 错误包装）。"""

import asyncio

import httpx
import pytest
from nova_ai.utils.http import (
    fetch_json,
    filter_headers_for_hook,
    json_headers,
    request_with_abort,
)
from nova_async import AbortController, AbortedError

pytestmark = pytest.mark.asyncio


def _patch_request(monkeypatch, handler):
    """替换 httpx.AsyncClient.request 为假实现（handler(method, url, **kwargs)）。"""

    async def fake_request(self, method, url, **kwargs):
        return await handler(method, str(url), **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)


class TestRequestWithAbort:
    async def test_transport_error_wrapped_as_runtime_error(self, monkeypatch):
        async def handler(method, url, **kwargs):
            raise httpx.ConnectError("connection refused")

        _patch_request(monkeypatch, handler)
        with pytest.raises(RuntimeError, match="connection refused"):
            await request_with_abort("GET", "https://x.test")

    async def test_returns_response_for_caller_status_handling(self, monkeypatch):
        async def handler(method, url, **kwargs):
            return httpx.Response(503, json={"error": "down"})

        _patch_request(monkeypatch, handler)
        response = await request_with_abort("GET", "https://x.test")
        assert response.status_code == 503

    async def test_method_and_headers_passed_through(self, monkeypatch):
        seen = {}

        async def handler(method, url, **kwargs):
            seen["method"] = method
            seen["headers"] = kwargs.get("headers")
            return httpx.Response(200, json={})

        _patch_request(monkeypatch, handler)
        await request_with_abort(
            "POST", "https://x.test", headers={"X-A": "b"}, json_body={"k": "v"}
        )
        assert seen["method"] == "POST"
        assert seen["headers"] == {"X-A": "b"}

    async def test_abort_midflight_raises_aborted_error(self, monkeypatch):
        started = asyncio.Event()
        closed = []

        async def handler(method, url, **kwargs):
            started.set()
            await asyncio.sleep(30)
            return httpx.Response(200)  # pragma: no cover

        async def fake_aclose(client):
            closed.append(client)

        _patch_request(monkeypatch, handler)
        monkeypatch.setattr(httpx.AsyncClient, "aclose", fake_aclose)
        controller = AbortController(name="test")
        task = asyncio.ensure_future(
            request_with_abort("GET", "https://x.test", signal=controller.signal)
        )
        await started.wait()
        controller.abort()
        with pytest.raises(AbortedError):
            await task
        # client 关闭在 AbortScope 外完整执行，不被同一个取消信号再次杀掉。
        assert len(closed) == 1

    async def test_pre_aborted_signal_rejects_immediately(self):
        controller = AbortController(name="test")
        controller.abort()
        with pytest.raises(AbortedError):
            await request_with_abort("GET", "https://x.test", signal=controller.signal)


class TestFetchJson:
    async def test_retryable_status_retried_then_success(self, monkeypatch):
        calls = []

        async def handler(method, url, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                return httpx.Response(500)
            return httpx.Response(200, json={"ok": True})

        _patch_request(monkeypatch, handler)
        result = await fetch_json("https://x.test")
        assert result == {"ok": True}
        assert len(calls) == 2

    async def test_non_retryable_status_raises_without_retry(self, monkeypatch):
        calls = []

        async def handler(method, url, **kwargs):
            calls.append(url)
            return httpx.Response(404)

        _patch_request(monkeypatch, handler)
        with pytest.raises(RuntimeError, match="404"):
            await fetch_json("https://x.test")
        assert len(calls) == 1

    async def test_transport_error_retried(self, monkeypatch):
        calls = []

        async def handler(method, url, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                raise httpx.ConnectError("reset")
            return httpx.Response(200, json={"ok": 1})

        _patch_request(monkeypatch, handler)
        result = await fetch_json("https://x.test")
        assert result == {"ok": 1}
        assert len(calls) == 2

    async def test_retry_exhaustion_raises(self, monkeypatch):
        calls = []

        async def handler(method, url, **kwargs):
            calls.append(url)
            return httpx.Response(500)

        _patch_request(monkeypatch, handler)
        with pytest.raises(RuntimeError, match="500"):
            await fetch_json("https://x.test")
        assert len(calls) == 3  # 首发 + 2 次重试

    async def test_abort_during_backoff(self, monkeypatch):
        async def handler(method, url, **kwargs):
            return httpx.Response(500)

        _patch_request(monkeypatch, handler)
        controller = AbortController(name="test")
        task = asyncio.ensure_future(
            fetch_json("https://x.test", signal=controller.signal)
        )
        await asyncio.sleep(0.05)  # 已进入首次失败后的退避期
        controller.abort()
        with pytest.raises(AbortedError):
            await task

    async def test_api_key_and_catalog_headers(self, monkeypatch):
        seen = {}

        async def handler(method, url, **kwargs):
            seen["headers"] = kwargs.get("headers")
            return httpx.Response(200, json={})

        _patch_request(monkeypatch, handler)
        await fetch_json("https://x.test", api_key="sk-t")
        headers = seen["headers"]
        assert headers["Authorization"] == "Bearer sk-t"
        assert headers["accept"] == "application/json"
        assert "nova-ai-model-data" in headers["User-Agent"]


class TestJsonHeaders:
    async def test_base_and_extra(self):
        headers = json_headers({"if-none-match": '"v1"'})
        assert headers["accept"] == "application/json"
        assert "nova-ai-model-data" in headers["User-Agent"]
        assert headers["if-none-match"] == '"v1"'

    async def test_no_extra(self):
        headers = json_headers()
        assert "if-none-match" not in headers


class TestFilterHeadersForHook:
    @pytest.mark.parametrize(
        "sensitive",
        [
            "authorization",
            "Authorization",
            "proxy-authorization",
            "x-api-key",
            "X-Api-Key",
            "set-cookie",
            "Set-Cookie",
            "cookie",
            "www-authenticate",
        ],
    )
    async def test_sensitive_headers_filtered_case_insensitively(self, sensitive):
        headers = filter_headers_for_hook(
            {sensitive: "secret", "content-type": "text/event-stream"}
        )
        assert sensitive not in headers
        assert headers == {"content-type": "text/event-stream"}

    async def test_normal_headers_preserved(self):
        headers = {
            "content-type": "text/event-stream",
            "retry-after": "30",
            "x-request-id": "req-1",
        }
        assert filter_headers_for_hook(headers) == headers
        # 返回新 dict，不改原对象
        assert filter_headers_for_hook(headers) is not headers
