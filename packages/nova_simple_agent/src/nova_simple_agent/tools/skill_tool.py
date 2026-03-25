import asyncio
from typing import Optional, List, Dict, Any
from pi_agent import AgentToolResult
from nova_ai import ImageContent, TextContent
from ..core import RemoteTool  # 导入RemoteTool基类

class RemoteSkillTool(RemoteTool):
    """技能工具 - 渐进式访问技能库"""
    
    def __init__(self):
        """
        初始化技能工具
        """
        super().__init__(
            name="skill_tool",
            description="访问和使用技能库。支持渐进式访问：先看分类，再看技能列表，最后看具体技能内容",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list_categories", "list_skills", "get_skill", "read_file"],
                        "description": "操作类型：list_categories(列出分类), list_skills(列出技能), get_skill(获取技能内容), read_file(读取技能文件)"
                    },
                    "category": {
                        "type": "string",
                        "description": "分类名称，如 'domain'、'system'。用于 list_skills 操作"
                    },
                    "skill_name": {
                        "type": "string",
                        "description": "技能名称。用于 get_skill 和 read_file 操作"
                    },
                    "filename": {
                        "type": "string",
                        "description": "要读取的文件名。仅用于 read_file 操作"
                    }
                },
                "required": ["action"]
            }
        )
        self.label = "技能库"

    async def execute(self, tool_call_id: str, params: dict,
                     signal: Optional[asyncio.Event] = None, on_update=None) -> AgentToolResult:
        """执行技能工具操作"""
        
        action = params.get("action")
        category = params.get("category")
        skill_name = params.get("skill_name")
        filename = params.get("filename")

        # 获取连接参数（从基类自动添加的）
        host = params.get("host", "localhost")
        port = params.get("port", 50001)

        try:
            # 检查是否已中止
            if signal and hasattr(signal, 'is_set') and signal.is_set():
                return AgentToolResult(
                    content=[TextContent(type="text", text="## ⚠️ 操作已中止")],
                    details={"error": "Operation aborted", "action": action}
                )

            # 获取远程proxy
            proxy = self._get_proxy(host, port)

            # 根据操作类型执行不同功能
            if action == "list_categories":
                # 远程调用list_categories
                result = proxy.list_categories()
                content = self._format_categories(result)
            
            elif action == "list_skills":
                if not category:
                    raise ValueError("列出技能需要提供 category 参数")
                # 远程调用list_skills
                cat_info = proxy.get_category_description(category)
                skills = proxy.list_skills_by_category(category)
                content = self._format_skills(category, cat_info, skills)
            
            elif action == "get_skill":
                if not skill_name:
                    raise ValueError("获取技能需要提供 skill_name 参数")
                # 远程调用get_skill_info, get_directory_tree, get_skill_content
                skill_info = proxy.get_skill_info(skill_name)
                if not skill_info:
                    raise ValueError(f"技能不存在: {skill_name}")
                
                directory_tree = proxy.get_directory_tree(skill_name)
                skill_content = proxy.get_skill_content(skill_name)
                
                content = self._format_skill(skill_info, directory_tree, skill_content)
            
            elif action == "read_file":
                if not skill_name or not filename:
                    raise ValueError("读取文件需要提供 skill_name 和 filename 参数")
                # 远程调用read_file
                file_data = proxy.read_file(skill_name, filename)
                return await self._handle_read_file(file_data, skill_name, filename)
            
            else:
                raise ValueError(f"不支持的操作类型: {action}")

            return AgentToolResult(
                content=[TextContent(type="text", text=content)],
                details={"action": action, "category": category, "skill_name": skill_name}
            )

        except Exception as e:
            error_msg = f"## ❌ 技能库操作失败\n\n{str(e)}"
            
            return AgentToolResult(
                content=[TextContent(type="text", text=error_msg)],
                details={"error": str(e), "action": action}
            )

    def _format_categories(self, categories: List[Dict]) -> str:
        """格式化分类列表为 Markdown"""
        if not categories:
            return "📭 暂无可用分类"
        
        lines = []
        lines.append("# 📚 技能分类\n")
        lines.append(f"共 **{len(categories)}** 个分类：\n")
        
        for cat in categories:
            lines.append(f"## 📁 {cat['name']}")
            if cat.get('description'):
                lines.append(f"\n{cat['description']}\n")
            else:
                lines.append("\n*暂无分类描述*\n")
        
        lines.append("\n---\n")
        lines.append("💡 **提示**: 使用 `list_skills` 查看具体分类下的技能\n")
        lines.append("```json\n{\n  \"action\": \"list_skills\",\n  \"category\": \"分类名称\"\n}\n```")
        
        return "\n".join(lines)

    def _format_skills(self, category: str, cat_info: Dict, skills: list) -> str:
        """格式化技能列表为 Markdown"""
        # skills_data 包含分类信息和技能列表
        if not skills:
            return f"📭 分类 `[{category}]` 下暂无技能"
        
        lines = []
        
        # 分类信息
        if cat_info:
            lines.append(f"# 📁 {cat_info.get('name', category)}\n")
            if cat_info.get('description'):
                lines.append(f"{cat_info['description']}\n")
        else:
            lines.append(f"# 📁 分类 `[{category}]`\n")
        
        # 技能列表
        lines.append(f"## 📦 技能列表 (共 {len(skills)} 个)\n")
        
        for skill in skills:
            lines.append(f"### 🔧 {skill['name']}")
            if skill.get('description'):
                lines.append(f"\n{skill['description']}\n")
            else:
                lines.append("\n*暂无技能描述*\n")
        
        lines.append("\n---\n")
        lines.append("💡 **提示**: 使用 `get_skill` 查看具体技能内容\n")
        lines.append("```json\n{\n  \"action\": \"get_skill\",\n  \"skill_name\": \"技能名称\"\n}\n```")
        
        return "\n".join(lines)

    def _format_skill(self, skill_info: Dict, directory_tree: str, skill_content: str) -> str:
        """格式化技能详细信息为 Markdown"""
        lines = []
        
        # ===== 技能元数据 =====
        lines.append(f"# 🔧 {skill_info['name']}\n")
        lines.append("## 📋 基本信息\n")
        lines.append(f"- **分类**: `{skill_info['category']}`")
        if skill_info.get('version'):
            lines.append(f"- **版本**: {skill_info['version']}")
        lines.append(f"- **路径**: `{skill_info.get('dir_path', '')}`")
        lines.append("")
        
        if skill_info.get('description'):
            lines.append("## 📝 技能描述\n")
            lines.append(f"{skill_info['description']}\n")
        
        # ===== 目录结构 =====
        lines.append("## 📂 目录结构\n")
        lines.append("```")
        lines.append(directory_tree)
        lines.append("```\n")
        
        # ===== SKILL.md 内容 =====
        lines.append("## 📄 SKILL.md\n")
        if skill_content:
            lines.append(skill_content)
        else:
            lines.append("*内容为空*\n")
        
        # ===== 使用提示 =====
        lines.append("\n---\n")
        lines.append("## 💡 使用提示\n")
        lines.append("1. 查看完 SKILL.md 后，可以使用 `read_file` 读取其他文件\n")
        lines.append("2. 文件列表请参考上面的目录结构\n")
        lines.append("3. 示例：\n")
        lines.append("```json")
        lines.append("{")
        lines.append('  "action": "read_file",')
        lines.append('  "skill_name": "' + skill_info['name'] + '",')
        lines.append('  "filename": "文件名"')
        lines.append("}")
        lines.append("```")
        
        return "\n".join(lines)

    async def _handle_read_file(self, file_data: str, skill_name: str, filename: str) -> AgentToolResult:
        """处理文件读取请求"""
        # 检查是否是错误信息（原函数返回的错误格式）
        if (file_data.startswith("Error:") or 
            file_data.startswith("[ERROR") or 
            file_data.startswith("[SECURITY ERROR")):
            return AgentToolResult(
                content=[TextContent(type="text", text=f"## ❌ 读取文件失败\n\n{file_data}")],
                details={"error": file_data, "action": "read_file", "skill_name": skill_name, "filename": filename}
            )
        
        # 格式化文件内容
        if filename.endswith('.md'):
            # Markdown 文件直接返回
            text_content = file_data
        else:
            # 其他文本文件用代码块包裹
            ext = filename.split('.')[-1] if '.' in filename else 'text'
            text_content = f"```{ext}\n{file_data}\n```"
        
        header = [
            f"# 📄 {filename}\n",
            f"**技能**: `{skill_name}`  ",
            f"**文件**: `{filename}`  \n",
            "---\n"
        ]
        
        return AgentToolResult(
            content=[TextContent(type="text", text="\n".join(header) + "\n" + text_content)],
            details={"action": "read_file", "skill_name": skill_name, "filename": filename}
        )