"""exec-server 环境注册表词汇（批次 A——词汇归枢纽）。

从 nova-exec-server-client 迁入：`environments.py` 的 `ResolvedEnvironment`
（跨包共享纯形状）。默认解析链（`resolve_environment`——有校验行为）留在
消费方 SDK，不进本包。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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
    #: 连接总时限（秒；None = 不限制）——仅 WS 环境合法（校验期
    #: connect_timeout_sec requires url）；与 initialize_timeout_sec 是
    #: 两笔独立预算（对位 codex：connect 管传输连接，缺省 10s）
    connect_timeout_sec: float | None = None
    #: initialize 握手时限（秒；None = 客户端缺省）——url/stdio 均合法
    #: （对位 codex：两种传输都带 initialize_timeout，缺省 10s，与连接
    #: 预算分账互不挤占）
    initialize_timeout_sec: float | None = None
    #: WS 鉴权 token（运行时解析产物——本地 spawn 的随机 bearer / SSH 隧道
    #: 的临时 token；不落盘、不进配置条目）
    token: str | None = None
