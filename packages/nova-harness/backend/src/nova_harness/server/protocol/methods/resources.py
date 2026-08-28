"""Resources 域 JSON-RPC 方法。

前端 slash 菜单与资源展示的数据源：prompt templates 与 skills 目录。
"""

from __future__ import annotations

from typing import Any, Dict, List

from nova_harness.server.protocol.errors import JSONRPCError
from nova_harness.server.protocol.methods import shapes
from nova_harness.server.protocol.methods.state import ServerState
from nova_harness.server.protocol.router import MethodRegistry


def serialize_source_info(source_info: Any) -> Dict[str, Any]:
    """SourceInfo（dataclass）→ JSON 安全 dict。"""
    if source_info is None:
        return {}
    return {
        "source": getattr(source_info, "source", None),
        "path": getattr(source_info, "path", None),
    }


def register(registry: MethodRegistry, state: ServerState) -> None:
    def _session() -> Any:
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        return state.runtime.session

    async def listPromptTemplates(
        params: shapes.EmptyParams,
    ) -> shapes.ListPromptTemplatesResult:
        """全部已加载 prompt templates（slash 菜单的模板展开项）。"""
        result = _session().resource_loader.get_prompts()
        prompts: List[Dict[str, Any]] = []
        for template in result.get("prompts", []):
            prompts.append(
                {
                    "name": getattr(template, "name", ""),
                    "description": getattr(template, "description", ""),
                    "argument_hint": getattr(template, "argument_hint", None),
                    "source": getattr(template, "source", None),
                    **serialize_source_info(getattr(template, "source_info", None)),
                }
            )
        return shapes.ListPromptTemplatesResult(prompts=prompts)

    async def listSkills(params: shapes.EmptyParams) -> shapes.ListSkillsResult:
        """全部已加载 skills。"""
        result = _session().resource_loader.get_skills()
        skills: List[Dict[str, Any]] = []
        for name, skill in sorted(result.get("skills", {}).items()):
            skills.append(
                {
                    "name": getattr(skill, "name", name),
                    "description": getattr(skill, "description", ""),
                    "file_path": getattr(skill, "file_path", None),
                    "source_label": getattr(skill, "source_label", None),
                }
            )
        return shapes.ListSkillsResult(skills=skills)

    _D = "resources"
    registry.register(
        "listPromptTemplates",
        listPromptTemplates,
        domain=_D,
    )
    registry.register(
        "listSkills",
        listSkills,
        domain=_D,
    )
