import os
from pathlib import Path

# =============================================================================
# App Config
# =============================================================================

APP_NAME = "nova"                     # 可通过环境变量 PI_APP_NAME 覆盖
CONFIG_DIR_NAME = ".nova"             # 可通过环境变量 PI_CONFIG_DIR 覆盖

# 环境变量名，例如 PI_AGENT_DIR
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

def get_sessions_dir() -> Path:
    """获取会话目录"""
    return get_agent_dir() / "sessions"

def get_prompts_dir() ->Path:
    """获取提示词模板目录"""
    return get_agent_dir() / "prompts"
