from abc import ABC, abstractmethod

from .diagnostics import ResourceDiagnostic
from .prompt_templates import PromptTemplate, load_prompt_templates
from .types import DefaultResourceLoaderOptions,LoadPromptTemplatesOptions  

class ResourceLoader(ABC):
    """资源加载器抽象基类（仅保留 prompts 相关）"""
    
    @abstractmethod
    def get_prompts(self) -> dict[str, list[PromptTemplate] | list[ResourceDiagnostic]]:
        """获取提示词模板和诊断信息"""
        pass
    
    @abstractmethod
    async def reload(self) -> None:
        """重新加载所有资源"""
        pass


class DefaultResourceLoader(ResourceLoader):
    """默认资源加载器实现（仅保留 prompt templates）"""
    
    def __init__(self, options: DefaultResourceLoaderOptions) -> None:
        self._cwd = options.cwd
        self._agent_dir = options.agent_dir
        self._additional_prompt_template_paths = options.additional_prompt_template_paths
        self._no_prompt_templates = options.no_prompt_templates
        
        self._prompts: list[PromptTemplate] = []
        self._prompt_diagnostics: list[ResourceDiagnostic] = []
    
    def get_prompts(self) -> dict[str, list[PromptTemplate] | list[ResourceDiagnostic]]:
        """获取提示词模板和诊断信息"""
        return {
            "prompts": self._prompts,
            "diagnostics": self._prompt_diagnostics,
        }
    
    async def reload(self) -> None:
        """重新加载所有资源"""
        prompt_paths = self._additional_prompt_template_paths
        self._update_prompts_from_paths(prompt_paths)
    
    def _update_prompts_from_paths(self, prompt_paths: list[str]) -> None:
        """从路径更新提示词模板"""
        if self._no_prompt_templates and not prompt_paths:
            self._prompts = []
            self._prompt_diagnostics = []
            return
        
        all_prompts = load_prompt_templates(
            LoadPromptTemplatesOptions(
                cwd=self._cwd,
                agent_dir=self._agent_dir,
                prompt_paths=prompt_paths,
                include_defaults=not self._no_prompt_templates,
            )
        )
        deduped = self._dedupe_prompts(all_prompts)
        self._prompts = deduped["prompts"]
        self._prompt_diagnostics = deduped["diagnostics"]
    
    def _dedupe_prompts(
        self, prompts: list[PromptTemplate]
    ) -> dict[str, list[PromptTemplate] | list[ResourceDiagnostic]]:
        """去重提示词模板并记录冲突"""
        seen: dict[str, PromptTemplate] = {}
        diagnostics: list[ResourceDiagnostic] = []
        
        for prompt in prompts:
            existing = seen.get(prompt.name)
            if existing:
                diagnostics.append({
                    "type": "collision",
                    "message": f'name "/{prompt.name}" collision',
                    "path": prompt.file_path,
                    "collision": {
                        "resource_type": "prompt",
                        "name": prompt.name,
                        "winner_path": existing.file_path,
                        "loser_path": prompt.file_path,
                    },
                })
            else:
                seen[prompt.name] = prompt
        
        return {
            "prompts": list(seen.values()),
            "diagnostics": diagnostics,
        }