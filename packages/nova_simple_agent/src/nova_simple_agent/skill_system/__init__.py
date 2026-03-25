"""
技能管理包
"""
from .registry import SkillMetadata, SkillRegistry
from .loader import SkillLoader
from .manager import SkillManager

__version__ = "1.0.0"
__all__ = [
    'SkillMetadata',
    'SkillRegistry',
    'SkillLoader',
    'SkillManager',
]