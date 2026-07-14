# Nova 资源加载器输出形式

本文档说明 `DefaultResourceLoader.reload()` 完成后，各类资源最终以什么 Python 对象形式存在，以及它们如何被上层（`SystemPromptManager`、`AgentSession`、`ToolsManager` 等）消费。

> 对应 TypeScript：`coding-agent/src/core/resource-loader.ts` 的 `DefaultResourceLoader`。

---

## 1. 总体流程

```
PackageManager.resolve_resources()   # 发现资源路径（内部调用 PackageResolver）
         │
         ▼
DefaultResourceLoader.reload()     # 加载/解析资源内容
         │
         ▼
各 get_*() 方法返回运行时可用对象
         │
         ▼
SystemPromptManager / ToolsManager / AgentSession 消费
```

---

## 2. 各类资源的最终形式

### 2.1 Agents — `get_agents()` / `get_agent_names()`

- **发现方式**：`PackageManager` 扫描 `agents/` 目录 + 已安装包内 `agents/`（通过内部 `PackageResolver`）
- **加载函数**：`resources/loaders/agent_config.py::load_agent_configs()`
- **最终形式**：
  ```python
  Dict[str, AgentConfig]
  ```
  key 为 agent 名称，value 为 `AgentConfig` 对象。
- **内容包含**：
  - `name`, `description`
  - `sections`: `List[Section]`（来自 `sections/*.md`）
  - `tools`: `List[ToolInfo]`
  - `skills`: `List[str]`（skill 白名单）
  - `extensions`: `List[str]`
  - `user_sections`: `List[Section]`（来自 `user/*.md`）
  - `setup_content`: `Optional[str]`（来自 `setup.md`）

### 2.2 Tools — `get_tools()`

- **发现方式**：`PackageManager` 扫描 `tools/` 目录 + 已安装包内 `tools/`（通过内部 `PackageResolver`）
- **加载函数**：`resources/loaders/tools.py::ToolLoader`
- **最终形式**：
  ```python
  Dict[str, Any]
  ```
  key 为 tool 名称，value 为动态加载的执行器对象或 `DynamicTool`。
- **消费方**：`ToolsManager` 激活后用于调用；`SystemPromptManager` 读取 `prompt_snippet` 和 `prompt_guidelines` 渲染 system prompt。

### 2.3 Skills — `get_skills()`

- **发现方式**：`PackageManager` 扫描 `skills/` 目录 + 已安装包内 `skills/` + 扩展贡献路径（通过内部 `PackageResolver`）
- **加载函数**：`resources/loaders/skills.py::load_skills()`
- **最终形式**：
  ```python
  Dict[str, Skill]
  ```
  key 为 skill 名称，value 为 `Skill` 对象。
- **Skill 字段**：
  - `name`, `description`
  - `file_path`: SKILL.md 完整路径
  - `base_dir`: SKILL.md 所在目录
  - `disable_model_invocation`: 是否禁止自动注入 system prompt
  - `source_label`, `source_info`
- **去重规则**：按 skill name 去重；同文件通过不同路径（如 symlink）引用时，用 `canonicalize_path` 识别为同一文件，避免重复加载。
- **消费方**：
  - `SystemPromptManager` 按当前 agent 的 `skills` 白名单过滤后，注入 system prompt
  - `AgentSession.prompt()` 支持 `/skill:name` 命令展开为 XML skill block

### 2.4 Prompt Templates — `get_prompts()`

- **发现方式**：`PackageManager` 扫描 `prompts/` 目录 + 已安装包内 `prompts/`（通过内部 `PackageResolver`）
- **加载函数**：`resources/loaders/prompt_templates.py::load_prompt_templates_with_diagnostics()`
- **最终形式**：
  ```python
  {
      "prompts": List[PromptTemplate],
      "diagnostics": List[ResourceDiagnostic],
  }
  ```
- **消费方**：运行时用户输入 `/template:name` 时由 `AgentSession` 展开替换。

### 2.5 Context Files — `get_context_files()`

- **发现方式**：直接扫描文件系统，**不经过 `PackageManager`**
  - 全局：`~/.nova/agent/AGENTS.md` / `~/.nova/agent/CLAUDE.md`
  - 项目级：从文件系统根目录向 `cwd` 遍历，每个目录中的 `AGENTS.md` / `CLAUDE.md`
- **加载函数**：`resources/loaders/context_files.py::load_project_context_files()`
- **最终形式**：
  ```python
  List[ContextFile]
  ```
- **ContextFile 字段**：
  - `path`: 文件路径
  - `content`: 文件内容
  - `source_info`: 可选来源信息
- **顺序**：全局优先，然后祖先目录由远及近，最后 `cwd`。
- **消费方**：`SystemPromptManager` 渲染为 `<project_context>` 块注入 system prompt。

### 2.6 Extensions — `get_extensions()`

- **发现方式**：`PackageManager` 扫描 `extensions/` 目录 + 已安装包内 `extensions/`（通过内部 `PackageResolver`）
- **加载函数**：`resources/loaders/extensions.py::load_extensions()`
- **最终形式**：
  ```python
  LoadedExtensionsResult
  ```
  包含：
  - `extensions`: `List[Extension]`
  - `diagnostics`: `List[Any]`
  - `runtime`: `Optional[ExtensionRuntime]`
- **消费方**：`ExtensionRunner` 管理扩展生命周期；扩展可注册 tools、commands、flags、shortcuts，并通过 `resources_discover` 贡献额外资源路径。

### 2.7 Themes — `get_themes()`

- **当前状态**：占位实现，返回空字典
- **最终形式**：
  ```python
  {
      "themes": {},
      "diagnostics": [],
  }
  ```
- **TS 对齐项**：未来需要实现主题加载（`loadThemesFromDir` 等价物）。

---

## 3. 覆盖回调

`DefaultResourceLoaderOptions` 提供以下回调，允许调用方在资源写入内部状态前修改结果：

- `extensions_override`
- `skills_override`
- `prompts_override`
- `tools_override`
- `agents_override`

这些回调与 TypeScript `DefaultResourceLoaderOptions` 对齐。

---

## 4. 开关控制

通过 `no_*` 字段可以关闭某类资源加载：

- `no_extensions`
- `no_tools`
- `no_prompt_templates`
- `no_skills`
- `no_themes`

其中 `no_themes` 默认为 `True`，因为主题系统尚未实现。

---

## 5. 与 System Prompt 的关系

`SystemPromptManager.build_system_prompt()` 从 `ResourceLoader` 获取：

- `get_agents()` → 当前 agent 配置
- `get_skills()` → 按 agent `skills` 白名单过滤后注入
- `get_tools()` / `ToolsManager` → 工具定义、snippet、guidelines
- `get_context_files()` → 项目上下文

最终由 `compose_system_prompt()` 组装成完整字符串，结构为：

1. Agent Description
2. System Instructions（sections）
3. Available Tools
4. Tool Guidelines
5. Meta（动态上下文）
6. User Context（user_sections）
7. Project Context（context files）
8. Skills

---

## 6. 与 Package Manager 的关系

- `PackageManager` 是统一 facade：对外暴露安装/卸载/列表能力，以及 `resolve_resources()` 资源解析能力。
- `PackageManager` 内部使用 `PackageResolver` 根据 settings + 自动发现 + 已安装包，解析出资源路径；缺失包的检测与安装也由 `PackageManager` 在调用 resolver 之前完成。
- `DefaultResourceLoader` 调用 `PackageManager.resolve_resources()`，并把路径加载为运行时对象。

editable 安装时，`PackageManager` 在 `packages/path/<name>/` 创建指向源码的 symlink；resolver 解析时返回 symlink 指向的真实源码目录，因此资源发现不会重复。

---

## 7. 当前已实现 vs 待补齐

| 资源类型 | 发现 | 加载 | 注入 system prompt | 备注 |
|----------|------|------|-------------------|------|
| agents | ✅ | ✅ | ✅ | agent-centric |
| tools | ✅ | ✅ | ✅ | |
| skills | ✅ | ✅ | ✅（按白名单） | `/skill:name` 已支持 |
| prompts | ✅ | ✅ | ❌ | 运行时 `/template` 展开 |
| context files | ✅ | ✅ | ✅ | 已对齐 TS |
| extensions | ✅ | ✅ | N/A | 通过 ExtensionRunner 消费 |
| themes | ❌ | ❌ | N/A | 占位，未实现 |

---

*文档生成时间：与当前代码同步。*
