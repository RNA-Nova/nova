# registry.py
from dataclasses import dataclass
from typing import Dict, Any, Optional, List

@dataclass
class SkillMetadata:
    name: str
    description: str
    category: str  # 统一的种类名，如 "domain"、"system"
    file_path: str
    dir_path: str
    version: str = "1.0.0"

@dataclass
class CategoryDesc:
    """分类描述"""
    name: str          # 种类名，如 "domain"
    description: str   # 该种类的说明

class SkillRegistry:
    def __init__(self):
        self.flat_map: Dict[str, SkillMetadata] = {}
        self.category_descs: Dict[str, CategoryDesc] = {}  # category name -> CategoryDesc

    def add_skill(self, metadata: SkillMetadata):
        self.flat_map[metadata.name] = metadata

    def add_category_desc(self, category_name: str, desc: CategoryDesc):
        """添加分类描述"""
        self.category_descs[category_name] = desc

    def get_skill(self, name: str) -> Optional[SkillMetadata]:
        return self.flat_map.get(name)

    def get_category_desc(self, category_name: str) -> Optional[CategoryDesc]:
        """获取指定种类的描述信息"""
        return self.category_descs.get(category_name)

    def list_skills(self, category_name: str) -> List[SkillMetadata]:
        """列出指定种类下的所有技能"""
        return [
            meta for meta in self.flat_map.values()
            if meta.category == category_name
        ]

    def list_categories(self) -> List[str]:
        """列出所有种类名"""
        return list(self.category_descs.keys())