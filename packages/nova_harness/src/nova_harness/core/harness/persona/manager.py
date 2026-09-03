"""PersonaManager —— persona 装配与 override 旋钮（人格资源的会话期消费点）。

职责（设计定案 §4/§6）：

1. **注册表视图**：persona 注册表来自 ``ResourceLoader.get_personas()``
   （包 + user/project 自动发现 + settings 条目 + 扩展贡献），本管理器只读；
2. **装配**（自 ``resources/loaders/agent_config.py`` 乔迁——按名引用必须等
   注册表就绪，故装配从加载期推迟到会话期）：``AgentConfig.persona`` 原始
   条目 → ``Section`` 序列。条目解析规则：
   - 能相对 **yaml 所在目录** 解析为文件/目录的按路径装配（文件直读、
     目录递归收 ``*.md`` 按相对路径字典序在该位置展开——顺序即组装顺序）；
     路径引用必须收敛在资源 ``base_dir``（包根/scope 基目录）内，逃逸即
     诊断并跳过；
   - 否则按注册名查 persona 注册表；
   - 都失败给 ``ResourceDiagnostic``；
3. **override 旋钮**（内存态）：``set_persona_override`` 生效时系统提示词的
   人格部分 = override persona 的单份 content（能力面不动）；``None`` 表示
   角色默认装配。持久化与分支安全由会话条目（``persona_override``）承担，
   归扩展命令层。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from nova_harness.core.types.protocols import ResourceLoaderProtocol
from nova_harness.core.types.resources.agents import AgentConfig, Section
from nova_harness.core.types.resources.diagnostics import ResourceDiagnostic
from nova_harness.core.types.resources.personas import Persona
from nova_harness.core.types.resources.selection import CapabilitySelection
from nova_harness.core.utils.files import load_text_file


def _collect_markdown_files(directory: Path) -> List[Path]:
    """目录条目展开：递归收 ``.md``，按相对路径字典序（01- 前缀即作者定序）。"""
    files = [p for p in directory.rglob("*.md") if p.is_file()]
    return sorted(files, key=lambda p: p.relative_to(directory).as_posix())


def _is_within(target: Path, root: Path) -> bool:
    """*target*（已 resolve）是否收敛在 *root*（已 resolve）内。"""
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass
class PersonaManager:
    """persona 装配与 override 管理（可变运行时容器，故 dataclass 而非 Pydantic）。

    注册表视图是**活视图**——``personas`` 每次访问现取 loader，reload 后
    无需手动刷新；override 为内存态，随会话/角色切换保留。
    """

    resource_loader: Optional[ResourceLoaderProtocol] = None
    _override: Optional[str] = None
    _last_diagnostics: List[ResourceDiagnostic] = field(default_factory=list)
    # 最近一次装配中"路径解析不了且注册表无此名"的 yaml 条目原文
    # （CapabilitySelection 报告的 missing 数据源）
    _last_missing: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # 注册表视图
    # ------------------------------------------------------------------

    @property
    def personas(self) -> Dict[str, Persona]:
        """persona 注册表活视图（{注册名: Persona}）。"""
        if self.resource_loader is None:
            return {}
        get_personas = getattr(self.resource_loader, "get_personas", None)
        if get_personas is None:
            return {}
        result = get_personas() or {}
        personas = result.get("personas") if isinstance(result, dict) else None
        # Mock/占位 loader 的防御：非 dict 一律视为空注册表
        if not isinstance(personas, dict):
            return {}
        return personas

    @property
    def last_diagnostics(self) -> List[ResourceDiagnostic]:
        """最近一次装配的诊断（路径缺失/逃逸包根/注册名未命中等）。"""
        return list(self._last_diagnostics)

    @property
    def selection_report(self) -> List[CapabilitySelection]:
        """persona 域的选配报告：yaml 条目按名引用查不到注册表 → ``missing``。

        persona 条目是装配引用（路径或注册名）而非名单，故只有 missing
        一种失败态；路径逃逸/读取失败等已有 ``last_diagnostics`` 覆盖，
        不重复进报告。
        """
        return [
            CapabilitySelection(resource_type="personas", name=entry, status="missing")
            for entry in self._last_missing
        ]

    # ------------------------------------------------------------------
    # override 旋钮
    # ------------------------------------------------------------------

    @property
    def current_override(self) -> Optional[str]:
        """当前 override 的 persona 注册名；``None`` = 角色默认装配。"""
        return self._override

    def set_persona_override(self, name: str) -> None:
        """设置 override（查注册表，找不到抛 ``ValueError``）。"""
        if name not in self.personas:
            available = ", ".join(sorted(self.personas)) or "(空)"
            raise ValueError(f"persona 不存在: {name}（注册表可用: {available}）")
        self._override = name

    def clear_persona_override(self) -> None:
        """清除 override，恢复角色默认装配。"""
        self._override = None

    # ------------------------------------------------------------------
    # 装配
    # ------------------------------------------------------------------

    def assemble(
        self, config: AgentConfig
    ) -> Tuple[List[Section], List[ResourceDiagnostic]]:
        """把 AgentConfig 的 persona 条目装配为 Section 序列（override 优先）。

        override 生效时人格部分 = override persona 的单份 content；override
        目标已不在注册表（reload 后被裁/卸载）时回退角色默认装配并记诊断。
        """
        diagnostics: List[ResourceDiagnostic] = []

        if self._override is not None:
            persona = self.personas.get(self._override)
            if persona is not None:
                sections = [
                    Section(
                        name=persona.name,
                        order=1,
                        content=persona.content,
                        source=persona.file_path,
                    )
                ]
                self._last_diagnostics = diagnostics
                self._last_missing = []
                return sections, diagnostics
            diagnostics.append(
                ResourceDiagnostic(
                    category="warning",
                    message=(
                        f"persona override '{self._override}' 不在注册表，"
                        "回退角色默认装配"
                    ),
                    path=config.agent_dir,
                )
            )

        sections, entry_diagnostics = self._assemble_entries(config)
        diagnostics.extend(entry_diagnostics)
        self._last_diagnostics = diagnostics
        return sections, diagnostics

    def _assemble_entries(
        self, config: AgentConfig
    ) -> Tuple[List[Section], List[ResourceDiagnostic]]:
        """逐条装配 yaml ``persona:`` 原始条目（路径优先，注册名兜底）。"""
        sections: List[Section] = []
        diagnostics: List[ResourceDiagnostic] = []
        self._last_missing = []

        base_dir = Path(config.agent_dir)
        # 包根收敛边界：source_info.base_dir（包根/scope 基目录）优先，
        # 缺省回退 yaml 所在目录
        containment = (
            Path(config.source_info.base_dir).resolve()
            if config.source_info and config.source_info.base_dir
            else base_dir.resolve()
        )

        def add_file(path: Path) -> None:
            content = load_text_file(str(path))
            if content is None:
                diagnostics.append(
                    ResourceDiagnostic(
                        category="warning",
                        message=f"persona 素材读取失败: {path}",
                        path=str(path),
                    )
                )
                return
            sections.append(
                Section(
                    name=path.stem,
                    order=len(sections) + 1,
                    content=content,
                    source=str(path),
                )
            )

        for item in config.persona or []:
            # 条目类型由 _parse_string_list 与 AgentConfig.persona: List[str]
            # 双重保证（纯字符串列表），无需运行期再校验
            entry = item.strip()
            if not entry:
                continue

            # 1) 路径装配：相对 yaml 所在目录可解析为文件/目录
            target = (base_dir / entry).resolve()
            if target.is_file() or target.is_dir():
                if not _is_within(target, containment):
                    diagnostics.append(
                        ResourceDiagnostic(
                            category="warning",
                            message=f"persona 路径逃逸包根（{containment}）: {target}",
                            path=str(target),
                        )
                    )
                    continue
                if target.is_file():
                    add_file(target)
                    continue
                files = _collect_markdown_files(target)
                if not files:
                    diagnostics.append(
                        ResourceDiagnostic(
                            category="warning",
                            message=f"persona 目录无 .md 素材: {target}",
                            path=str(target),
                        )
                    )
                for file_path in files:
                    add_file(file_path)
                continue

            # 2) 注册名装配：查 persona 注册表
            persona = self.personas.get(entry)
            if persona is not None:
                sections.append(
                    Section(
                        name=persona.name,
                        order=len(sections) + 1,
                        content=persona.content,
                        source=persona.file_path,
                    )
                )
                continue

            diagnostics.append(
                ResourceDiagnostic(
                    category="warning",
                    message=f"persona 条目无法解析（路径不存在且注册表无此名）: {entry}",
                    path=str(target),
                )
            )
            self._last_missing.append(entry)

        return sections, diagnostics


__all__ = ["PersonaManager"]
