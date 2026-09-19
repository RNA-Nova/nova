"""网络裁决门——`network/policyRequest` 回调的可复用底座（UI-free）。

定位与边界：

- 服务端对**未列名**主机发起 ask 裁决（静态 allow/deny 名单由 executor
  代理自己按 `networkProxy` 配置评估，不进本门）；
- 本门提供三件套：**会话记忆表**（"本会话记住"）+ **ask 注入点**
  （`on_ask` 回调，UI 弹窗归调用方实现）+ **fail-closed 兜底**（无注入/
  `approval_policy = "never"`/无 UI 时 ask 一律降级 deny——对位 codex：
  prompt 在 never 下视为拒绝）；
- 本层不产生任何交互——弹窗回路永远归调用方（如 nova 的 bundle 扩展）。

典型用法：

```python
gate = NetworkPolicyGate(on_ask=my_ui_ask)   # my_ui_ask: async (params) -> AskOutcome
client = ExecutorClient(url, network_policy=gate.decide)
```
"""

from __future__ import annotations

from enum import Enum
from typing import Awaitable, Callable

from .config import ApprovalPolicy
from .policy import resolve_ask_behavior
from .protocol import NetworkPolicyDecision, NetworkPolicyRequestParams

#: deny 理由（进审计通知，面向用户）
REASON_NOT_LISTED = "主机不在网络放行名单"
REASON_ASK_UNAVAILABLE = (
    "主机未列名，且当前无可询问渠道（approval_policy=never 或无 UI）"
)


class AskOutcome(str, Enum):
    """用户对一次 ask 的裁决（含"本会话记住"语义）"""

    ALLOW = "allow"
    DENY = "deny"
    ALLOW_REMEMBER = "allow-remember"
    DENY_REMEMBER = "deny-remember"


#: ask 注入点签名：收裁决请求，回用户裁决
OnAsk = Callable[[NetworkPolicyRequestParams], Awaitable[AskOutcome]]


class NetworkPolicyGate:
    """网络裁决门：会话记忆 + ask 注入 + fail-closed 兜底。

    记忆表按**精确主机名**键控（模式匹配归配置文件静态名单，服务端评估）；
    会话级内存——`snapshot()`/`restore()` 供调用方做分支安全持久化
    （如 nova 的会话条目）。
    """

    def __init__(
        self,
        on_ask: OnAsk | None = None,
        *,
        approval_policy: ApprovalPolicy = ApprovalPolicy.ON_REQUEST,
    ) -> None:
        self._on_ask = on_ask
        self._approval_policy = approval_policy
        self._memory: dict[str, bool] = {}

    async def decide(self, params: NetworkPolicyRequestParams) -> NetworkPolicyDecision:
        """network/policyRequest 裁决入口（直接挂 ExecutorClient(network_policy=...)）"""
        host = params.request.host

        remembered = self._memory.get(host)
        if remembered is not None:
            return (
                NetworkPolicyDecision.allow()
                if remembered
                else NetworkPolicyDecision.deny(REASON_NOT_LISTED)
            )

        behavior = resolve_ask_behavior(
            self._approval_policy, ui_available=self._on_ask is not None
        )
        if behavior == "deny":
            return NetworkPolicyDecision.deny(REASON_ASK_UNAVAILABLE)

        assert self._on_ask is not None
        outcome = await self._on_ask(params)
        if outcome is AskOutcome.ALLOW_REMEMBER:
            self._memory[host] = True
        elif outcome is AskOutcome.DENY_REMEMBER:
            self._memory[host] = False
        if outcome in (AskOutcome.ALLOW, AskOutcome.ALLOW_REMEMBER):
            return NetworkPolicyDecision.allow()
        return NetworkPolicyDecision.deny(REASON_NOT_LISTED)

    # ------------------------------------------------------------------
    # 会话记忆表（调用方持久化用）
    # ------------------------------------------------------------------

    def remember(self, host: str, *, allow: bool) -> None:
        """程序化记忆（如 /network 命令直加）"""
        self._memory[host] = allow

    def forget(self, host: str) -> bool:
        """移除记忆条目；返回是否真的移除了"""
        return self._memory.pop(host, None) is not None

    def snapshot(self) -> dict[str, bool]:
        """记忆表快照（{host: allow}——供会话条目持久化）"""
        return dict(self._memory)

    def restore(self, entries: dict[str, bool]) -> None:
        """从快照恢复（供分支/会话恢复）"""
        self._memory.update(entries)
