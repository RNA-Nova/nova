"""executor 配置根 loader——executor 栈客户端半边的发现层。

定位（定案）：执行策略词汇（沙箱套餐/网络代理/审批档）归 executor 栈自持，
不进 agent core——harness settings 不携带任何执行词汇。本模块是 PROTOCOL
v1.4 `environmentConfig/read` 层栈的客户端半边，同两层、同格式：

- user 层：`<executor home>/config.toml`（TOML；home = `NOVA_EXECUTOR_HOME`
  覆盖，缺省 `~/.nova/executor`）
- project 层：`<cwd>/.nova/settings.json` 的 `executor` 段（JSON；
  **仅当 `project_trusted=True` 时读取**——Project Trust 裁决归 harness，
  本层只消费布尔结论，不做信任判断）

合并纪律（对位 codex `config/src/merge.rs`）：表（dict）按键深合并；
**列表与标量整体覆盖**（高层替换低层——codex 语义：legacy arrays replace
wholesale，不做列表追加）。未知键 warn-and-ignore（前向兼容——新版写旧版
读不炸）；文件缺失 = 空层；解析失败 = ConfigError（配置是用户资产，坏文件
要响亮地报，不静默吞）。

远程说明：本 loader 读**客户端机器**的盘——策略在客户端物化、展开后经
process/start 下发，executor 不合并不裁决。需要执行机本机配置的场景走
`environmentConfig/read` 代读端点（本模块不管）。
"""

from __future__ import annotations

import json
import logging
import os
import tomllib
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from .errors import ConfigError
from .protocol import NetworkMode

logger = logging.getLogger(__name__)

#: executor 家目录环境变量覆盖（对位 rust executor 的同名旋钮）
NOVA_EXECUTOR_HOME_ENV = "NOVA_EXECUTOR_HOME"

#: user 层配置文件名（位于 executor home 下；对位 executor-protocol v1.4 层栈）
USER_CONFIG_FILE_NAME = "config.toml"

#: project 层位置（与 nova 体系项目级配置一致；executor 词汇住其中的 executor 段）
PROJECT_CONFIG_DIR_NAME = ".nova"
PROJECT_CONFIG_FILE_NAME = "settings.json"
PROJECT_SECTION_KEY = "executor"


class SandboxMode(str, Enum):
    """文件系统沙箱套餐名（配置词汇，永不上线——上线的是展开对象）"""

    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"


class SandboxWorkspaceWriteConfig(BaseModel):
    """workspace-write 套餐微调旋钮（逐字段对位 codex SandboxWorkspaceWrite）"""

    writable_roots: list[str] = Field(default_factory=list)
    network_access: bool = False
    exclude_tmpdir_env_var: bool = False
    exclude_slash_tmp: bool = False


class NetworkProxySettings(BaseModel):
    """`[network_proxy]` 段（nova 自有词汇——codex config 无对应键（其托管
    网络由组织/云配置驱动）；字段形状照线上 RemoteNetworkProxyConfig 推导）"""

    enabled: bool = False
    #: 托管模式："proxy" = 经代理按名单放行；"none" = 无网络访问全拒
    mode: NetworkMode = NetworkMode.PROXY
    allowed_domains: list[str] = Field(default_factory=list)
    denied_domains: list[str] = Field(default_factory=list)


class ApprovalPolicy(str, Enum):
    """审批档（对位 codex AskForApproval；untrusted 已被上游废弃，不收）"""

    ON_REQUEST = "on-request"
    ON_FAILURE = "on-failure"
    NEVER = "never"


class ExecutorConfig(BaseModel):
    """合并后的有效 executor 配置（物化层的输入）。

    词汇平铺对位 codex config.toml：`sandbox_mode` / `[sandbox_workspace_write]`
    / `approval_policy`（+ nova 自有的 `[network_proxy]`）。project 层
    （`.nova/settings.json` 的 `executor` 段）内为同一平铺词汇的 JSON 形态。
    """

    #: 沙箱套餐档（缺席 = 不物化、不下发——保持 nova 现状：未配置不沙箱，
    #: executor 按自身缺省姿态执行。注：codex 对 trusted 目录默认
    #: workspace-write——产品姿态差异，刻意不跟）
    sandbox_mode: SandboxMode | None = None
    sandbox_workspace_write: SandboxWorkspaceWriteConfig = Field(
        default_factory=SandboxWorkspaceWriteConfig
    )
    network_proxy: NetworkProxySettings | None = None
    approval_policy: ApprovalPolicy = ApprovalPolicy.ON_REQUEST


def default_executor_home() -> Path:
    """executor 家目录：`NOVA_EXECUTOR_HOME` 覆盖，缺省 `~/.nova/executor`"""
    override = os.environ.get(NOVA_EXECUTOR_HOME_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".nova" / "executor"


def load_executor_config(
    cwd: str | Path | None = None,
    *,
    project_trusted: bool = False,
    executor_home: str | Path | None = None,
) -> ExecutorConfig:
    """加载并合并 executor 配置层栈。

    - `cwd`：项目根（定位 project 层）；为 None 时只读 user 层
    - `project_trusted`：Project Trust 结论（harness 裁决后传入）——False 时
      project 层即使存在也不读（恶意仓库无法经配置弱化执行姿态）
    - `executor_home`：测试/特殊部署的显式覆盖（优先级高于环境变量）
    """
    home = Path(executor_home) if executor_home is not None else default_executor_home()
    layers: list[tuple[str, dict[str, Any]]] = []

    user_file = home / USER_CONFIG_FILE_NAME
    layers.append((f"user:{user_file}", _read_toml_layer(user_file)))

    if cwd is not None and project_trusted:
        project_file = Path(cwd) / PROJECT_CONFIG_DIR_NAME / PROJECT_CONFIG_FILE_NAME
        layers.append((f"project:{project_file}", _read_project_layer(project_file)))

    merged: dict[str, Any] = {}
    for _source, content in layers:
        merged = _merge_layer(merged, content)
    return _validate(merged, sources=[source for source, _ in layers])


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------


def _read_toml_layer(path: Path) -> dict[str, Any]:
    """user 层（TOML）：缺失 = 空层；解析失败 = ConfigError"""
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"executor user 层配置解析失败（{path}）：{error}") from error
    if not isinstance(data, dict):  # TOML 恒 dict，防御性断言
        raise ConfigError(f"executor user 层配置必须是表（{path}）")
    return data


def _read_project_layer(path: Path) -> dict[str, Any]:
    """project 层：`<cwd>/.nova/settings.json` 的 `executor` 段"""
    if not path.is_file():
        return {}
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(
            f"executor project 层配置解析失败（{path}）：{error}"
        ) from error
    if not isinstance(settings, dict):
        raise ConfigError(f"project settings 必须是对象（{path}）")
    section = settings.get(PROJECT_SECTION_KEY)
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise ConfigError(f"project 层 `{PROJECT_SECTION_KEY}` 段必须是对象（{path}）")
    return section


def _merge_layer(low: dict[str, Any], high: dict[str, Any]) -> dict[str, Any]:
    """单层合并（对位 codex merge.rs）：表按键深合并；列表/标量整体覆盖"""
    out = dict(low)
    for key, value in high.items():
        existing = out.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            out[key] = _merge_layer(existing, value)
        else:
            out[key] = value
    return out


def _validate(merged: dict[str, Any], *, sources: list[str]) -> ExecutorConfig:
    _warn_unknown_keys(merged, ExecutorConfig, path=())
    try:
        config = ExecutorConfig.model_validate(merged)
    except ValidationError as error:
        raise ConfigError(
            f"executor 配置校验失败（层来源：{', '.join(sources)}）：{error}"
        ) from error
    return config


def _warn_unknown_keys(
    data: dict[str, Any], model: type[BaseModel], *, path: tuple[str, ...]
) -> None:
    """未知键 warn-and-ignore（逐段递归，对位 codex 的前向兼容纪律）"""
    for key, value in data.items():
        field = model.model_fields.get(key)
        dotted = ".".join((*path, key))
        if field is None:
            logger.warning("executor 配置含未知键（忽略）：%s", dotted)
            continue
        # 已知键且值为 dict 且目标字段是 BaseModel 子类 → 递归检查
        annotation = field.annotation
        target = _model_class(annotation)
        if isinstance(value, dict) and target is not None:
            _warn_unknown_keys(value, target, path=(*path, key))


def _model_class(annotation: Any) -> type[BaseModel] | None:
    """从字段注解里剥出 BaseModel 子类（穿 Optional/Union）"""
    import typing

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in typing.get_args(annotation):
        if isinstance(arg, type) and issubclass(arg, BaseModel):
            return arg
    return None
