"""环境选择编排（自 bundle `executor_switch` 升格——对位 codex core
`environment_selection.rs` 的选择编排职责）。

nova 的解析链（定案，四层）：

```
会话覆盖（会话条目 executor_backend）
  → agent 组合声明默认（yaml 的 executor/environment 字段——缺席跳过）
    → 配置默认（`~/.nova/exec-server/config.toml` 的 default_environment）
      → 内建 local
```

codex 的 Turn 级选择机（1975 行——每回合 cwd/workspace_roots/config 归属/
连接事件转发）按 nova 的会话模型收窄：选择即"本会话用哪个环境"的一次性
解析；环境管理件（连接/缓存/状态）继续在 nova-exec-server-client 的
EnvironmentManager，本模块只放选择编排，零连接管理。
"""

from __future__ import annotations

from dataclasses import dataclass

from nova_exec_server_client import resolve_environment
from nova_protocol import ExecutorConfig, ResolvedEnvironment


def resolve_environment_selection(
    config: ExecutorConfig,
    *,
    session_override: ResolvedEnvironment | None = None,
    agent_default: str | None = None,
) -> ResolvedEnvironment:
    """环境选择解析链（四层，逐层短路）。

    - `session_override`：会话条目恢复的显式选择（最高优先——用户在本
      会话用 /executor 切过）；
    - `agent_default`：agent 组合声明的默认环境 id（yaml 字段——解析失败
      不炸，落下一层）；
    - 兜底：`resolve_environment(config)`（SDK 的配置默认链——
      default_environment → include_local → local）。
    """
    if session_override is not None:
        return session_override

    if agent_default is not None:
        try:
            return resolve_environment(config, agent_default)
        except Exception:
            # agent 声明了不存在的环境——组合错误但不该炸会话，落配置默认层
            pass

    return resolve_environment(config)
