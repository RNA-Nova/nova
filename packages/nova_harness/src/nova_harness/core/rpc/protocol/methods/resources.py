"""Resources 域 JSON-RPC 方法。

前端 slash 菜单与资源展示的数据源：prompt templates 与 skills 目录。
"""

from __future__ import annotations

from typing import Any, Dict

from nova_harness.core.rpc.protocol.errors import JSONRPCError
from nova_harness.core.rpc.protocol.methods import shapes as _sh
from nova_harness.core.rpc.protocol.methods.state import ServerState
from nova_harness.core.rpc.protocol.router import MethodRegistry

_D = "resources"


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
        params: _sh.EmptyParams,
    ) -> _sh.ListPromptTemplatesResult:
        """全部已加载 prompt templates（slash 菜单的模板展开项）。"""
        result = _session().resource_loader.get_prompts()
        prompts = []
        for template in result.get("prompts", []):
            # source 字段以 source_info 为准（缺席时回退模板自带 source）
            source_info = getattr(template, "source_info", None)
            source = (
                getattr(source_info, "source", None)
                if source_info is not None
                else getattr(template, "source", None)
            )
            prompts.append(
                _sh.PromptTemplateInfo(
                    name=getattr(template, "name", ""),
                    description=getattr(template, "description", ""),
                    argument_hint=getattr(template, "argument_hint", None),
                    source=source,
                )
            )
        return _sh.ListPromptTemplatesResult(prompts=prompts)

    async def listSkills(params: _sh.EmptyParams) -> _sh.ListSkillsResult:
        """全部已加载 skills。"""
        result = _session().resource_loader.get_skills()
        skills = []
        for name, skill in sorted(result.get("skills", {}).items()):
            skills.append(
                _sh.SkillInfo(
                    name=getattr(skill, "name", name),
                    description=getattr(skill, "description", ""),
                    file_path=getattr(skill, "file_path", None),
                    source_label=getattr(skill, "source_label", None),
                )
            )
        return _sh.ListSkillsResult(skills=skills)

    registry.register("listPromptTemplates", listPromptTemplates, domain=_D)
    registry.register("listSkills", listSkills, domain=_D)
