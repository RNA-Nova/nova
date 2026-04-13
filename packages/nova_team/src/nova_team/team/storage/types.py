# teammanager/storage/types.py

"""
Mounts scope definitions - 两级
"""

from enum import Enum


class MountScope(Enum):
    """两级作用域."""
    GLOBAL = "global"      # {agent_dir}/mounts.json
    PROJECT = "project"    # {cwd}/.kimi/mounts.json