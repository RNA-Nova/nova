"""环境标识与解析后环境值对象（无配置依赖的叶子模块）。"""

from dataclasses import dataclass
from typing import Literal

#: 内建本地环境 id（对位 codex LOCAL_ENVIRONMENT_ID）
LOCAL_ENVIRONMENT_ID = "local"


@dataclass(frozen=True)
class ResolvedEnvironment:
    """解析后的环境（transport 构造参数已归位；frozen 值对象）"""

    id: str
    kind: Literal["local", "ws", "stdio"]
    url: str | None = None
    program: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] | None = None
    cwd: str | None = None
    connect_timeout_sec: float | None = None
    initialize_timeout_sec: float | None = None
    token: str | None = None
