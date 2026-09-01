"""环境注册表解析——多 executor 的客户端原语（对位 codex environments 体系）。

codex 对位关系：`~/.codex/environments.toml`（exec-server crate 自持解析）
+ `EnvironmentDefault` 解析。我们把注册表词汇合并在同一个
`~/.nova/executor/config.toml`（层栈已定单文件），`[[environments]]` 条目
字段逐一对位 codex `EnvironmentToml`。

选择/切换编排（哪个会话用哪个环境）不归本层——归调用方（对位 codex core
的 environment_selection；nova 里将来归 bundle 扩展）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .config import ExecutorConfig, ExecutorEnvironment
from .errors import ConfigError

#: 内建本地环境 id（对位 codex LOCAL_ENVIRONMENT_ID）
LOCAL_ENVIRONMENT_ID = "local"


@dataclass(frozen=True)
class ResolvedEnvironment:
    """解析后的环境（transport 构造参数已归位；frozen 值对象）"""

    id: str
    kind: Literal["local", "ws", "stdio"]
    #: kind="ws" 时的 WS URL
    url: str | None = None
    #: kind="stdio" 时的 spawn 命令（SSH 承载：program="ssh"）
    program: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] | None = None
    cwd: str | None = None
    #: 连接总时限（秒；None = 不限制）
    connect_timeout_sec: float | None = None


def resolve_environment(
    config: ExecutorConfig, name: str | None = None
) -> ResolvedEnvironment:
    """按名解析环境；`name=None` 走默认解析链（对位 codex
    normalize_default_environment_id + include_local 语义）：

    - `default_environment` 已设 → 按它解析（"none" = 禁用默认，报错）；
    - 未设 → `include_local=True` 时落内建 local，否则报错。
    """
    if name is None:
        default = config.default_environment
        if default is not None and default.strip().lower() == "none":
            raise ConfigError(
                '默认环境已禁用（default_environment = "none"）——请显式指定'
            )
        name = default or LOCAL_ENVIRONMENT_ID
        if default is None and not config.include_local:
            raise ConfigError("未配置默认环境且 include_local=false——请显式指定")

    if name == LOCAL_ENVIRONMENT_ID:
        if not config.include_local:
            raise ConfigError("内建 local 环境已被 include_local=false 禁用")
        return ResolvedEnvironment(id=LOCAL_ENVIRONMENT_ID, kind="local")

    for environment in config.environments:
        if environment.id == name:
            return _to_resolved(environment)
    available = [e.id for e in config.environments]
    raise ConfigError(
        f"未知环境 `{name}`（已注册：{', '.join(available) or '（空）'}；"
        f"内建：{LOCAL_ENVIRONMENT_ID if config.include_local else '（已禁用）'}）"
    )


def _to_resolved(environment: ExecutorEnvironment) -> ResolvedEnvironment:
    if environment.url is not None:
        return ResolvedEnvironment(
            id=environment.id,
            kind="ws",
            url=environment.url.strip(),
            connect_timeout_sec=environment.connect_timeout_sec,
        )
    assert environment.program is not None  # load 期已校验 url/program 二选一
    return ResolvedEnvironment(
        id=environment.id,
        kind="stdio",
        program=environment.program.strip(),
        args=tuple(environment.args),
        env=dict(environment.env),
        cwd=environment.cwd,
        connect_timeout_sec=environment.connect_timeout_sec,
    )
