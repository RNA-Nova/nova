# loader.py
import re
import yaml
from pathlib import Path
from typing import Any, Tuple, Optional, Dict, Union
from .registry import SkillMetadata, SkillRegistry, CategoryDesc

# Optional: for pretty printing in terminal
try:
    from rich.tree import Tree
except ImportError:
    Tree = Any

class SkillLoader:
    def __init__(self, encoding: str = 'utf-8'):
        self.encoding = encoding
        self.ignore = {'.git', '__pycache__', '.idea', '.vscode', 'venv', '.DS_Store'}

    def _parse_frontmatter(self, path: Path) -> Tuple[Optional[Dict], int]:
        """
        解析文件的 frontmatter。
        返回 (frontmatter_dict, content_start_position)
        如果只有 frontmatter 没有内容，content_start_position 指向文件末尾
        """
        try:
            content = path.read_text(encoding=self.encoding)
            
            # 匹配 --- 开头的内容，支持只有 frontmatter 没有后续内容的情况
            match = re.search(r'^---\s*\n(.*?)\n---\s*\n?', content, re.DOTALL | re.MULTILINE)
            
            if not match:
                return None, 0
                
            yaml_content = match.group(1)
            
            try:
                parsed = yaml.safe_load(yaml_content)
                # 计算内容开始位置
                content_start = match.end()
                return parsed, content_start
                
            except yaml.YAMLError:
                return None, 0
                
        except Exception:
            return None, 0

    def scan_to_registry(self, root_path: Path, registry: SkillRegistry):
        """扫描根目录下的所有种类"""
        if not root_path.exists(): return

        # 遍历根目录的下一级目录（种类目录）
        for item in sorted(root_path.iterdir()):
            if item.name in self.ignore: continue
            if not item.is_dir(): continue  # 只处理目录
            
            category_name = item.name  # 如 domain, system
            print(f"📁 [Category] Scanning: {category_name}")
            
            # 检查种类目录下是否有 description.md
            desc_file = item / "description.md"
            if desc_file.exists():
                meta_dict, _ = self._parse_frontmatter(desc_file)
                if meta_dict and 'name' in meta_dict:
                    # 有有效的 frontmatter，使用其中的 name
                    category_name = meta_dict['name']  # 用 description.md 中定义的 name
                    desc = CategoryDesc(
                        name=category_name,
                        description=meta_dict.get('description', '')
                    )
                    registry.add_category_desc(category_name, desc)
                    print(f"  📋 [Desc] Loaded: {desc.name} - {desc.description[:50]}...")
                else:
                    # 有文件但解析失败或无name字段，使用目录名
                    desc = CategoryDesc(
                        name=category_name,
                        description=''
                    )
                    registry.add_category_desc(category_name, desc)
                    print(f"  ⚠️  [Desc] Failed to parse, using dirname: {category_name}")
            else:
                # 没有 description.md 文件，使用目录名
                desc = CategoryDesc(
                    name=category_name,
                    description=''
                )
                registry.add_category_desc(category_name, desc)
                print(f"  ⚠️  [Desc] No description.md, using dirname: {category_name}")
            
            # 扫描该种类下的所有技能
            self._scan_category(item, category_name, registry)

    def _scan_category(self, category_path: Path, category_name: str, registry: SkillRegistry):
        """递归扫描种类目录下的技能"""
        for item in sorted(category_path.iterdir()):
            if item.name in self.ignore: continue
            
            if item.is_dir():
                skill_file = item / "SKILL.md"
                if skill_file.exists():
                    # 这是一个技能包
                    meta_dict, offset = self._parse_frontmatter(skill_file)
                    if meta_dict:
                        meta = SkillMetadata(
                            name=meta_dict.get('name', item.name),
                            description=meta_dict.get('description', ''),
                            category=category_name,  # 使用统一的种类名
                            file_path=str(skill_file.absolute()),
                            dir_path=str(item.absolute()),
                            version=meta_dict.get('version', '1.0.0')
                        )
                        registry.add_skill(meta)
                        print(f"  📦 [Skill] Added: {meta.name}")
                    else:
                        # 有 SKILL.md 但解析失败，使用目录名和空描述
                        meta = SkillMetadata(
                            name=item.name,
                            description='',
                            category=category_name,
                            file_path=str(skill_file.absolute()),
                            dir_path=str(item.absolute()),
                            version='1.0.0'
                        )
                        registry.add_skill(meta)
                        print(f"  ⚠️  [Skill] Failed to parse, using dirname: {item.name}")
                else:
                    # 没有 SKILL.md，递归扫描子目录
                    self._scan_category(item, category_name, registry)

    def get_skill_content(self, skill_name: str, registry: SkillRegistry) -> str:
        """
        获取技能文件的内容（去掉 frontmatter）
        """
        meta = registry.get_skill(skill_name)
        if not meta:
            return ""
        
        path = Path(meta.file_path)
        if not path.exists():
            return ""
        
        _, offset = self._parse_frontmatter(path)
        if offset == 0:
            # 没有 frontmatter，返回全部内容
            return path.read_text(encoding=self.encoding)
        
        # 有 frontmatter，返回后面的内容
        content = path.read_text(encoding=self.encoding)
        return content[offset:].strip()

    def get_ascii_tree(self, path: Union[str, Path], prefix: str = "") -> str:
        """
        Generates a clean ASCII tree for the LLM to understand file structure.
        支持传入字符串或Path对象
        """
        # 将字符串转换为Path对象
        if isinstance(path, str):
            path = Path(path)
        
        lines = []
        if prefix == "":
            lines.append(f"📁 {path.name}/")
        
        # Sort directories first, then files
        items = sorted([x for x in path.iterdir() if x.name not in self.ignore], 
                      key=lambda x: (not x.is_dir(), x.name.lower()))
        
        for i, item in enumerate(items):
            is_last = (i == len(items) - 1)
            connector = "└── " if is_last else "├── "
            
            if item.is_dir():
                lines.append(f"{prefix}{connector}📁 {item.name}/")
                extension = "    " if is_last else "│   "
                lines.append(self.get_ascii_tree(item, prefix + extension))
            else:
                icon = "📝" if item.name == "SKILL.md" else "📄"
                # 特殊标记 description.md
                if item.name == "description.md":
                    icon = "📋"
                lines.append(f"{prefix}{connector}{icon} {item.name}")
                
        return "\n".join(lines)

    def get_rich_tree(self, path: Union[str, Path], tree_obj: Optional[Any] = None) -> Any:
        """Generates a Rich Tree object for terminal visualization."""
        if Tree is Any: return "Rich library not installed."
        
        # 将字符串转换为Path对象
        if isinstance(path, str):
            path = Path(path)
        
        if tree_obj is None:
            tree_obj = Tree(f"📁 [bold cyan]{path.name}[/bold cyan]")
        
        for item in sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if item.name in self.ignore: continue
            if item.is_dir():
                branch = tree_obj.add(f"📁 [blue]{item.name}[/blue]")
                self.get_rich_tree(item, branch)
            else:
                icon = "📝" if item.name == "SKILL.md" else "📄"
                if item.name == "description.md":
                    icon = "📋"
                tree_obj.add(f"{icon} {item.name}")
        return tree_obj