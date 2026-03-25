# manager.py
import json
import re
from pathlib import Path
from typing import Any, List, Dict, Optional, Union
from .loader import SkillLoader
from .registry import SkillRegistry

class SkillManager:
    """
    基础设施层 (Infrastructure Layer) - 纯文件管理
    仅负责技能文件的扫描、注册和读取，不包含任何LLM能力
    """
    def __init__(self, skills_root: str):
        self.root_path = Path(skills_root)
        self.loader = SkillLoader()
        self.registry = SkillRegistry()
        self._initialize()

    def _initialize(self):
        """扫描并注册所有技能文件"""
        print(f"🔄 [Manager] Scanning skills from: {self.root_path}")
        self.loader.scan_to_registry(self.root_path, self.registry)
        count = len(self.registry.flat_map)
        categories = len(self.registry.category_descs)
        print(f"✅ [Manager] Registry ready. Loaded {count} skills in {categories} categories.")

    # --- 种类描述相关方法 ---
    def get_category_description(self, category_name: str) -> Optional[Dict[str, str]]:
        """
        获取指定种类的描述信息
        例如: get_category_description('domain')
        """
        desc = self.registry.get_category_desc(category_name)
        if not desc:
            return None
        return {
            "name": desc.name,
            "description": desc.description
        }

    def list_categories(self) -> List[Dict[str, str]]:
        """列出所有种类及其描述"""
        return [
            {
                "name": cat_name,
                "description": self.registry.get_category_desc(cat_name).description
            }
            for cat_name in self.registry.list_categories()
        ]

    # --- 基础工具 ---
    def get_skill_content(self, skill_name: str) -> str:
        """
        获取技能主文件(SKILL.md)的内容
        """
        return self.loader.get_skill_content(skill_name, self.registry)

    def get_skill_manifest(self, category_name: str) -> str:
        """
        获取指定种类下所有技能的清单
        """
        skills = self.registry.list_skills(category_name)
        if not skills: 
            return f"No skills available in {category_name}."
        
        return "\n".join([f"- {s.name}: {s.description}" for s in skills])

    def get_skill_info(self, skill_name: str) -> Optional[Dict[str, Any]]:
        """
        获取技能的元数据信息
        """
        meta = self.registry.get_skill(skill_name)
        if not meta:
            return None
        
        return {
            "name": meta.name,
            "category": meta.category,
            "description": meta.description,
            "dir_path": str(meta.dir_path),
            "file_path": str(meta.file_path),
            "version": meta.version
        }

    def list_skills_by_category(self, category_name: str) -> List[Dict[str, str]]:
        """
        按种类列出技能
        """
        skills = self.registry.list_skills(category_name)
        return [{"name": s.name, "description": s.description} for s in skills]

    def get_directory_tree(self, skill_name: str) -> str:
        """
        获取技能目录的ASCII树结构
        """
        meta = self.registry.get_skill(skill_name)
        if not meta:
            return "Skill not found."
        
        return self.loader.get_ascii_tree(meta.dir_path)

    def read_file(self, skill_name: str, filename: str) -> str:
        """
        根据 skill_name 和 filename 获取文件原始内容。
        包含安全检查，防止读取技能目录以外的文件。
        """
        meta = self.registry.get_skill(skill_name)
        if not meta: 
            return "Error: Skill not found."
        
        skill_dir = Path(meta.dir_path)
        target = (skill_dir / filename).resolve()
        
        # 安全检查：确保目标文件在技能目录内
        if not str(target).startswith(str(skill_dir.resolve())):
            return "[SECURITY ERROR] Cannot access files outside skill directory"
        
        if not target.exists():
            return "[ERROR: File not found]"
        
        if not target.is_file():
            return "[ERROR: Path is not a file]"
        
        return target.read_text(encoding='utf-8')

    def get_skill_structure(self, skill_name: str) -> Dict[str, Any]:
        """
        获取技能的完整结构信息
        """
        meta = self.registry.get_skill(skill_name)
        if not meta:
            return {"error": "Skill not found"}
        
        skill_dir = Path(meta.dir_path)
        structure = {
            "name": meta.name,
            "category": meta.category,
            "description": meta.description,
            "version": meta.version,
            "files": []
        }
        
        # 列出目录下的所有文件（不包括子目录）
        for item in skill_dir.iterdir():
            if item.is_file():
                structure["files"].append({
                    "name": item.name,
                    "size": item.stat().st_size,
                    "modified": item.stat().st_mtime
                })
        
        return structure

    def find_skill_by_content(self, search_text: str) -> List[Dict[str, str]]:
        """
        在技能文件中搜索特定文本
        """
        results = []
        for skill_name, meta in self.registry.flat_map.items():
            try:
                content = self.get_skill_content(skill_name)
                if search_text.lower() in content.lower():
                    results.append({
                        "name": skill_name,
                        "description": meta.description,
                        "category": meta.category
                    })
            except Exception:
                continue  # 跳过读取失败的技能
        
        return results

    def get_skills_summary(self) -> Dict[str, List[Dict[str, str]]]:
        """
        获取所有技能的种类摘要
        """
        summary = {}
        
        for category_name in self.registry.list_categories():
            skills = self.registry.list_skills(category_name)
            if skills:
                summary[category_name] = [
                    {"name": s.name, "description": s.description} 
                    for s in skills
                ]
        
        return summary

    def reload_category(self, category_name: str) -> bool:
        """
        重新加载指定种类的所有技能
        """
        if category_name not in self.registry.list_categories():
            return False
        
        # 重新扫描该种类目录
        category_dir = self.root_path / category_name
        self.loader._scan_category(category_dir, category_name, self.registry)
        return True