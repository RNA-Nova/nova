"""Persona 资源类型定义（persona 升格：人格文本从"素材"升为资源类目）。

Persona 是一份可被发现的 Markdown 人格文本，命名 = 相对 personas 根的
路径去 ``.md`` 扩展名（posix 形态，如 ``coding/core``、``subagents/scout``）。

与 prompts 的边界（设计定案 §4）：prompts = 用户命令宏（零提示词占用）；
persona = 角色身份文本（装配进系统提示词）；skills = 模型自主能力说明书。
概念不合并，发现管线同源。
"""

from typing import Optional

from nova_ai.types.base_model import NovaBaseModel

from nova_harness.core.types.extensions import SourceInfo


class Persona(NovaBaseModel):
    """一个已加载的 persona（人格文本资源）。"""

    name: str
    content: str
    file_path: str
    # 来源（包/用户/项目……）——收集层 resolver 的 provenance 透传
    source_info: Optional[SourceInfo] = None


__all__ = ["Persona"]
