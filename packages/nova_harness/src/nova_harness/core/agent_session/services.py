"""
AgentSession 的 cwd 绑定服务集合。

- 负责创建 cwd 绑定的基础设施（auth/settings/model_runtime/resourceLoader）。
- 收集创建过程中的 diagnostics（扩展 provider 注册失败等）。
- ``AgentSession`` 与 ``AgentSessionRuntime`` 通过本对象共享依赖。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from nova_ai.signal import AbortController

from nova_harness.core.config import AuthStorage, SettingsManager
from nova_harness.core.config.defaults import (
    AUTH_FILE_NAME,
    MODELS_FILE_NAME,
    get_agent_dir,
)
from nova_harness.core.config.migration import migrate_backend_layout
from nova_harness.core.extensions.api import NovaExtensionAPI
from nova_harness.core.model import ModelRuntime
from nova_harness.core.package import PackageManager
from nova_harness.core.resources.loader import DefaultResourceLoader, ResourceLoader
from nova_harness.core.types.extensions import ExtensionFlag, LoadedExtensionsResult
from nova_harness.core.types.resources.loader import DefaultResourceLoaderOptions
from nova_harness.core.types.resources.tools import ToolContext
from nova_harness.core.types.session.diagnostics import AgentSessionRuntimeDiagnostic
from nova_harness.core.types.session.factory import CreateAgentSessionRuntimeResult
from nova_harness.core.utils.timings import time

__all__ = ["AgentSessionServices"]


@dataclass
class AgentSessionServices:
    """
    与某个 cwd/session 绑定的服务集合。

    这些服务在 AgentSession 生命周期内相对稳定；
    切换会话时 Runtime 可以选择复用或重新创建 services。
    """

    cwd: str
    agent_dir: str
    settings_manager: SettingsManager = field(default=None)  # type: ignore[assignment]
    model_runtime: ModelRuntime = field(default=None)  # type: ignore[assignment]
    resource_loader: ResourceLoader = field(default=None)  # type: ignore[assignment]
    auth_storage: AuthStorage = field(default=None)  # type: ignore[assignment]
    diagnostics: List[AgentSessionRuntimeDiagnostic] = field(default_factory=list)

    @classmethod
    async def create(
        cls,
        cwd: str,
        agent_dir: Optional[Union[str, Path]] = None,
        auth_storage: Optional[AuthStorage] = None,
        settings_manager: Optional[SettingsManager] = None,
        model_runtime: Optional[ModelRuntime] = None,
        resource_loader: Optional[ResourceLoader] = None,
        extension_flag_values: Optional[Dict[str, Any]] = None,
        install_missing_packages: bool = True,
        additional_skill_paths: Optional[List[str]] = None,
        additional_prompt_template_paths: Optional[List[str]] = None,
        on_progress: Optional[Callable[[Any], None]] = None,
        project_trusted: Optional[bool] = None,
        resolve_project_trust: Optional[
            Callable[[LoadedExtensionsResult], Awaitable[bool]]
        ] = None,
    ) -> "AgentSessionServices":
        """
        创建 cwd 绑定的服务集合。

        返回的 services 已经包含 authStorage、settingsManager、model_runtime、
        resourceLoader（已 reload）。扩展由 ResourceLoader 加载，
        AgentSession 在初始化时从 ResourceLoader 取出扩展并创建 ExtensionRunner。

        ``on_progress`` 会透传给 ``PackageManager``——会话启动时自愈重装
        settings 中缺失包的进度事件（SDK/RPC 可桥接到 UI 通知）。
        """
        resolved_cwd = str(Path(cwd).resolve())
        resolved_agent_dir = (
            str(Path(agent_dir).resolve())
            if agent_dir
            else str(Path(get_agent_dir()).resolve())
        )
        # 目录布局迁移（前后端分治 §9）：旧位散养资源目录（<base>/extensions 等）
        # 整体搬入 <base>/backend/ 半区——幂等，需在 settings/资源解析之前完成。
        migrate_backend_layout(cwd=resolved_cwd, agent_dir=resolved_agent_dir)
        auth_storage = auth_storage or AuthStorage.create(
            Path(resolved_agent_dir) / AUTH_FILE_NAME
        )

        # 未显式指定信任状态时，默认先不信任项目；由 resolve_project_trust 回调
        # 或后续流程决定最终是否信任。
        needs_trust_resolution = resolve_project_trust is not None
        initial_project_trusted = (
            project_trusted if project_trusted is not None else False
        )

        if settings_manager is None:
            settings_manager = SettingsManager.create(
                resolved_cwd,
                resolved_agent_dir,
                project_trusted=initial_project_trusted,
            )
        elif project_trusted is not None:
            settings_manager.set_project_trusted(initial_project_trusted)

        # 内建官方包通道（冻结形态：首启落地 + 登记进 settings 包清单；
        # 开发态零动作）——须在资源解析之前
        from nova_harness.core.package.builtin import ensure_builtin_packages

        ensure_builtin_packages(settings_manager, resolved_agent_dir)

        # 冻结形态的包运行时装配路径挂载（.site 依赖 + 各包 backend/），
        # 须在资源加载/工具 import 之前
        from nova_harness.core.package.runtime_paths import ensure_package_paths

        ensure_package_paths(
            resolved_agent_dir,
            settings_manager,
            project_base_dir=str(Path(resolved_cwd) / ".nova"),
        )

        model_runtime = model_runtime or ModelRuntime(
            auth_storage, Path(resolved_agent_dir) / MODELS_FILE_NAME
        )
        # 包 LLM 工具的构造期上下文：cwd + settings 活视图（不变量）；
        # 执行期的当前模型由 ToolExecContext 经 execute 第 5 参注入
        # （AgentSession.get_tool_exec_context → refresh 时包装）。
        tool_context = ToolContext(cwd=resolved_cwd, settings=settings_manager)
        # 启动时做一次动态模型网络刷新（15s 上限，对齐 TS ModelRuntime.create），
        # 离线（NOVA_OFFLINE）时只读 models-store 缓存；同时精确刷新可用性快照。
        controller = AbortController()
        timer = asyncio.get_running_loop().call_later(15, controller.abort)
        try:
            await model_runtime.refresh(signal=controller.signal)
        finally:
            timer.cancel()

        if resource_loader is None:
            package_manager = PackageManager(
                agent_dir=resolved_agent_dir,
                cwd=resolved_cwd,
                settings_manager=settings_manager,
                project_trusted=initial_project_trusted,
                install_missing_packages=install_missing_packages,
                on_progress=on_progress,
            )
            resource_loader = DefaultResourceLoader(
                DefaultResourceLoaderOptions(
                    cwd=resolved_cwd,
                    agent_dir=resolved_agent_dir,
                    settings_manager=settings_manager,
                    model_runtime=model_runtime,
                    tool_context=tool_context,
                    additional_prompt_template_paths=additional_prompt_template_paths
                    or [],
                    additional_skill_paths=additional_skill_paths or [],
                    additional_extension_paths=[],
                    no_prompt_templates=False,
                    no_extensions=False,
                    extension_api_factory=lambda extension, context: NovaExtensionAPI(
                        extension,
                        context,
                        cwd=getattr(context, "cwd", resolved_cwd),
                        event_bus=getattr(context, "event_bus", None),
                    ),
                    package_manager=package_manager,
                    install_missing_packages=install_missing_packages,
                )
            )
            if needs_trust_resolution:
                # 先以不信任状态加载全局/临时扩展，供信任裁决使用；
                # 返回的结果会在最终 reload 中复用，避免扩展被加载两次。
                pre_trust_extensions = (
                    await resource_loader.load_project_trust_extensions()
                )
                time("resource loader pre-trust extensions")
                trusted = await resolve_project_trust(pre_trust_extensions)
                settings_manager.set_project_trusted(trusted)
                await settings_manager.reload()
                await resource_loader.reload(pre_trust_extensions=pre_trust_extensions)
                time("resource loader trusted reload")
            else:
                await resource_loader.reload()
                time("resource loader initial reload")

        diagnostics: List[AgentSessionRuntimeDiagnostic] = []
        extensions_result = resource_loader.get_extensions()
        for err in getattr(extensions_result, "errors", None) or []:
            diagnostics.append(
                AgentSessionRuntimeDiagnostic(
                    type="error",
                    message=f'Extension "{err.get("path", "<unknown>")}" '
                    f'failed to load: {err.get("error", "")}',
                )
            )
        cls._flush_pending_provider_registrations(
            extensions_result, model_runtime, diagnostics
        )
        flag_diagnostics = cls._apply_extension_flag_values(
            extensions_result, extension_flag_values
        )
        diagnostics.extend(flag_diagnostics)

        return cls(
            cwd=resolved_cwd,
            agent_dir=resolved_agent_dir,
            settings_manager=settings_manager,
            model_runtime=model_runtime,
            resource_loader=resource_loader,
            auth_storage=auth_storage,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _flush_pending_provider_registrations(
        extensions_result: LoadedExtensionsResult,
        model_runtime: ModelRuntime,
        diagnostics: List[AgentSessionRuntimeDiagnostic],
    ) -> None:
        """把扩展加载阶段排队的 provider 注册刷新到 model_runtime。"""
        runtime = extensions_result.runtime
        if runtime is None:
            return
        for reg in list(runtime.pending_provider_registrations):
            try:
                model_runtime.register_provider(reg.name, reg.config)
            except Exception as error:
                message = str(error)
                diagnostics.append(
                    AgentSessionRuntimeDiagnostic(
                        type="error",
                        message=f'Extension "{reg.extension_path or "<unknown>"}" '
                        f'provider "{reg.name}" registration failed: {message}',
                    )
                )
        runtime.pending_provider_registrations.clear()

    @staticmethod
    def _apply_extension_flag_values(
        extensions_result: LoadedExtensionsResult,
        extension_flag_values: Optional[Dict[str, Any]],
    ) -> List[AgentSessionRuntimeDiagnostic]:
        """应用 CLI 传入的扩展 flag 值。"""
        diagnostics: List[AgentSessionRuntimeDiagnostic] = []
        if not extension_flag_values:
            return diagnostics

        registered_flags: Dict[str, ExtensionFlag] = {}
        for extension in extensions_result.extensions:
            for flag in extension.flags.values():
                if flag.name not in registered_flags:
                    registered_flags[flag.name] = flag

        runtime = extensions_result.runtime
        unknown_flags: List[str] = []
        for name, value in extension_flag_values.items():
            flag = registered_flags.get(name)
            if flag is None:
                unknown_flags.append(name)
                continue

            if flag.type == "boolean":
                # 布尔型 flag 作为开关：只要指定就视为 true
                if runtime is not None:
                    runtime.flag_values[name] = True
                continue

            if flag.type == "string":
                if isinstance(value, str):
                    if runtime is not None:
                        runtime.flag_values[name] = value
                    continue
                diagnostics.append(
                    AgentSessionRuntimeDiagnostic(
                        type="error",
                        message=f'Extension flag "--{name}" requires a string value',
                    )
                )
                continue

            if flag.type == "number":
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    if runtime is not None:
                        runtime.flag_values[name] = value
                    continue
                diagnostics.append(
                    AgentSessionRuntimeDiagnostic(
                        type="error",
                        message=f'Extension flag "--{name}" requires a numeric value',
                    )
                )
                continue

        if unknown_flags:
            suffix = "" if len(unknown_flags) == 1 else "s"
            diagnostics.append(
                AgentSessionRuntimeDiagnostic(
                    type="error",
                    message=f"Unknown option{suffix}: "
                    f'{", ".join(f"--{name}" for name in unknown_flags)}',
                )
            )

        return diagnostics
