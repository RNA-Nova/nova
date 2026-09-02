import os
from pathlib import Path
from typing import Optional, Union

from nova_ai import ModelThinkingLevel

# =============================================================================
# App Config
# =============================================================================

APP_NAME = os.environ.get("NOVA_APP_NAME", "nova")  # 可通过环境变量 NOVA_APP_NAME 覆盖
CONFIG_DIR_NAME = os.environ.get(
    "NOVA_CONFIG_DIR", ".nova"
)  # 可通过环境变量 NOVA_CONFIG_DIR 覆盖

# 用户未设置默认思考级别时的兜底值（对齐 TS defaults.ts 的 DEFAULT_THINKING_LEVEL）
DEFAULT_THINKING_LEVEL = ModelThinkingLevel.MEDIUM

# 环境变量名，例如 NOVA_AGENT_DIR
ENV_AGENT_DIR = f"{APP_NAME.upper()}_AGENT_DIR"

# 资源子目录名（集中管理，避免各模块硬编码）
AGENTS_DIR_NAME = "agents"
TOOLS_DIR_NAME = "tools"
PROMPTS_DIR_NAME = "prompts"
SKILLS_DIR_NAME = "skills"
EXTENSIONS_DIR_NAME = "extensions"
USER_TOOLS_DIR_NAME = "user_tools"
PERSONAS_DIR_NAME = "personas"
PACKAGES_DIR_NAME = "packages"
SESSIONS_DIR_NAME = "sessions"

# 包安装子目录
PATH_PACKAGES_DIR_NAME = "path"
GIT_PACKAGES_DIR_NAME = "git"
NPM_PACKAGES_DIR_NAME = "npm"

# 配置文件名（集中管理）
SETTINGS_FILE_NAME = "settings.json"
AUTH_FILE_NAME = "auth.json"
MODELS_FILE_NAME = "models.json"
MODELS_STORE_FILE_NAME = "models-store.json"
TRUST_FILE_NAME = "trust.json"


# =============================================================================
# User Config Paths
# =============================================================================


def get_agent_dir() -> Path:
    """获取全局 agent 配置目录（例如 ~/.nova/agent/）"""
    env_dir = os.environ.get(ENV_AGENT_DIR)
    if env_dir:
        return Path(env_dir).expanduser()
    return Path.home() / CONFIG_DIR_NAME / "agent"


def get_project_config_dir(cwd: Optional[Union[str, Path]] = None) -> Path:
    """获取项目级配置目录（例如 <cwd>/.nova/）。

    该目录同时作为项目级 package / 资源的基准目录。项目级 settings 文件直接
    放在该目录下，而不是其下的 agent/ 子目录中。
    """
    cwd = cwd or os.getcwd()
    return Path(cwd).resolve() / CONFIG_DIR_NAME


def get_project_base_dir(cwd: Optional[Union[str, Path]] = None) -> Path:
    """获取项目级 Nova 数据根目录（<cwd>/.nova）。

    与 ``get_project_config_dir`` 相同，但语义上强调这是项目级 package、
    git 缓存、extensions、skills、prompts 等资源的根目录。
    """
    return get_project_config_dir(cwd)


def get_agents_dir() -> Path:
    """获取全局 Agent 组合声明目录（``~/.nova/agent/agents``——两半共享，平级保留）"""
    return get_agent_dir() / AGENTS_DIR_NAME


def get_packages_dir() -> Path:
    """获取 package manager 缓存/元数据目录"""
    return get_agent_dir() / PACKAGES_DIR_NAME


def get_sessions_dir() -> Path:
    """获取全局会话目录"""
    return get_agent_dir() / SESSIONS_DIR_NAME


__all__ = [
    "APP_NAME",
    "CONFIG_DIR_NAME",
    "DEFAULT_THINKING_LEVEL",
    "ENV_AGENT_DIR",
    "AGENTS_DIR_NAME",
    "TOOLS_DIR_NAME",
    "PROMPTS_DIR_NAME",
    "SKILLS_DIR_NAME",
    "EXTENSIONS_DIR_NAME",
    "USER_TOOLS_DIR_NAME",
    "PERSONAS_DIR_NAME",
    "PACKAGES_DIR_NAME",
    "SESSIONS_DIR_NAME",
    "PATH_PACKAGES_DIR_NAME",
    "GIT_PACKAGES_DIR_NAME",
    "SETTINGS_FILE_NAME",
    "AUTH_FILE_NAME",
    "MODELS_FILE_NAME",
    "MODELS_STORE_FILE_NAME",
    "TRUST_FILE_NAME",
    "get_agent_dir",
    "get_project_config_dir",
    "get_project_base_dir",
    "get_agents_dir",
    "get_packages_dir",
    "get_sessions_dir",
]
