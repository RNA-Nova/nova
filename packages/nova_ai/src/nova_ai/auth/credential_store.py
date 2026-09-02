"""CredentialStore 实现。

对齐 TypeScript ``src/auth/credential-store.ts``：
默认提供内存实现；应用可注入持久化存储。
"""

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..types.auth import Credential, CredentialInfo, CredentialStore


class InMemoryCredentialStore(CredentialStore):
    """内存凭证存储。

    按 ``provider_id`` 串行化写操作，与 TS ``InMemoryCredentialStore`` 对齐。
    """

    def __init__(self) -> None:
        self._credentials: Dict[str, Credential] = {}
        self._chains: Dict[str, Any] = {}

    def _enqueue(
        self,
        provider_id: str,
        task: Callable[[], Awaitable[Any]],
    ) -> Any:
        previous = self._chains.get(provider_id)
        if previous is None:
            previous = _resolved_promise()

        async def _run() -> Any:
            try:
                await previous
            except Exception:
                pass
            return await task()

        # 链上必须存可重复 await 的 Task：裸协程只能 await 一次，
        # 后续调用者 await 同一协程会 RuntimeError（被静默吞掉），
        # 串行化随之失效（对齐 TS 链上存 next.catch(() => {})）。
        next_task = asyncio.ensure_future(_run())
        self._chains[provider_id] = next_task
        return next_task

    async def read(self, provider_id: str) -> Optional[Credential]:
        return self._credentials.get(provider_id)

    async def list(self) -> List[CredentialInfo]:
        return [
            CredentialInfo(provider_id=provider_id, type=credential.type)
            for provider_id, credential in self._credentials.items()
        ]

    async def modify(
        self,
        provider_id: str,
        fn: Callable[[Optional[Credential]], Awaitable[Optional[Credential]]],
    ) -> Optional[Credential]:
        async def _task() -> Optional[Credential]:
            current = self._credentials.get(provider_id)
            next_credential = await fn(current)
            if next_credential is not None:
                self._credentials[provider_id] = next_credential
                return next_credential
            return current

        return await self._enqueue(provider_id, _task)

    async def delete(self, provider_id: str) -> None:
        async def _task() -> None:
            self._credentials.pop(provider_id, None)

        await self._enqueue(provider_id, _task)


def _resolved_promise() -> Any:
    """返回一个已经 resolve 的 future。"""
    future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    future.set_result(None)
    return future


__all__ = ["InMemoryCredentialStore"]
