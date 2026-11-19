"""Gateway 与上游配置模型及 YAML 加载。"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import yaml
from nova_protocol import Model, NovaBaseModel
from pydantic import ConfigDict, Field


class UpstreamConfig(NovaBaseModel):
    """单个上游 Provider；`models` 为空时使用内置 Provider 的聊天目录。"""

    model_config = ConfigDict(extra="forbid")

    provider: str
    name: Optional[str] = None
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None
    # 自定义 OpenAI 兼容上游的完整聊天模型定义；为空表示使用内置 Provider。
    models: List[Model] = Field(default_factory=list)


class GatewayConfig(NovaBaseModel):
    """服务监听、下游鉴权、上游与公共目录配置（缺省列表均为空集）。"""

    model_config = ConfigDict(extra="forbid")

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    # Gateway Key 只经请求头使用，调试 repr 不得泄露。
    api_token: Optional[str] = Field(default=None, repr=False)
    allow_origins: List[str] = Field(default_factory=list)
    upstreams: List[UpstreamConfig] = Field(default_factory=list)
    # 空列表 = 所有已注册聊天模型均可调用；非空 = 仅这些 provider/id。
    allow_models: List[str] = Field(default_factory=list)
    # 缺省使用 ~/.nova/agent/gateway/catalog。
    catalog_dir: Optional[str] = None


def load_config(path: str | Path) -> GatewayConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError("Gateway config must be a YAML object")
    return GatewayConfig.model_validate(raw)


__all__ = ["GatewayConfig", "UpstreamConfig", "load_config"]
