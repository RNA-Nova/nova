import os
from pathlib import Path

# =============================================================================
# App Config
# =============================================================================

APP_NAME = "nova"  # 可通过环境变量 NOVA_APP_NAME 覆盖
CONFIG_DIR_NAME = ".nova"  # 可通过环境变量 NOVA_CONFIG_DIR 覆盖

# 环境变量名，例如 NOVA_AGENT_DIR
ENV_AGENT_DIR = f"{APP_NAME.upper()}_AGENT_DIR"

# =============================================================================
# User Config Paths
# =============================================================================


def get_agent_dir() -> Path:
    """获取 agent 配置目录（例如 ~/.nova/agent/）"""
    env_dir = os.environ.get(ENV_AGENT_DIR)
    if env_dir:
        return Path(env_dir).expanduser()
    return Path.home() / CONFIG_DIR_NAME / "agent"


def get_agents_dir() -> Path:
    """获取 Agent 配置目录（例如 ~/.nova/agent/agents/）"""
    return get_agent_dir() / "agents"


def get_tools_dir() -> Path:
    """获取工具定义目录（例如 ~/.nova/agent/tools/）"""
    return get_agent_dir() / "tools"


def get_prompts_dir() -> Path:
    """获取提示词模板目录"""
    return get_agent_dir() / "prompts"


def get_skills_dir() -> Path:
    """获取 skill 目录"""
    return get_agent_dir() / "skills"


def get_extensions_dir() -> Path:
    """获取扩展目录"""
    return get_agent_dir() / "extensions"


def get_packages_dir() -> Path:
    """获取 package manager 缓存/元数据目录"""
    return get_agent_dir() / "packages"


def get_sessions_dir() -> Path:
    """获取会话目录"""
    return get_agent_dir() / "sessions"
