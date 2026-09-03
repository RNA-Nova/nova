# TypeScript 资源加载层参考（`resource-loader.ts`）

> 对应源码：`/root/pi/packages/coding-agent/src/core/resource-loader.ts` 及其下游加载器。

## 1. 资源加载层在整条链路中的位置

```
安装层（package-manager.ts）
    │  安装 npm / git / local 包，写入 settings.packages
    │  包目录保留在安装位置，不拆分到 skills/ 等子目录
    ▼
解析层（package-manager.resolve / resolveExtensionSources）
    │  根据 settings.packages 生成 ResolvedResource[]
    │  资源类型：extensions / skills / prompts / themes
    ▼
加载层（resource-loader.ts）  ← 本文档重点
    │  把 ResolvedResource.path 读入内存，实例化为具体对象
    ▼
使用层（Runner / Agent / TUI）
       Extension[] / Skill[] / PromptTemplate[] / Theme[]
```

`DefaultResourceLoader` 是入口类。构造时持有 `DefaultPackageManager`、`SettingsManager`、`EventBus`，调用 `reload()` 后通过 getter 暴露已加载的资源实例。

## 2. 加载产物：每种资源最终变成什么字段的实例

| 资源类型 | 源文件 | 加载函数 | 产物类型 | 最终存放字段 |
|---|---|---|---|---|
| **Extensions** | `.ts` / `.js`（含 `index.ts`） | `loadExtensions()` | `Extension` 对象 | `LoadExtensionsResult.extensions: Extension[]` |
| **Skills** | `SKILL.md` 或普通 `.md` | `loadSkills()` | `Skill` 对象 | `{ skills: Skill[], diagnostics: ResourceDiagnostic[] }` |
| **Prompts** | `.md` | `loadPromptTemplates()` | `PromptTemplate` 对象 | `{ prompts: PromptTemplate[], diagnostics: ResourceDiagnostic[] }` |
| **Themes** | `theme.json` | `loadThemeFromPath()` | `Theme` 类实例 | `{ themes: Theme[], diagnostics: ResourceDiagnostic[] }` |
| **Agent Context** | `AGENTS.md` / `CLAUDE.md` | `loadProjectContextFiles()` | 原始 `{ path, content }` 数组 | `{ agentsFiles: Array<{ path: string; content: string }> }` |
| **System Prompt** | 文件或字符串 | `resolvePromptInput()` | `string` | `systemPrompt?: string` / `appendSystemPrompt: string[]` |

## 3. `DefaultResourceLoader` 对外暴露的 getter

```ts
export interface ResourceLoader {
    getExtensions(): LoadExtensionsResult;
    getSkills(): { skills: Skill[]; diagnostics: ResourceDiagnostic[] };
    getPrompts(): { prompts: PromptTemplate[]; diagnostics: ResourceDiagnostic[] };
    getThemes(): { themes: Theme[]; diagnostics: ResourceDiagnostic[] };
    getAgentsFiles(): { agentsFiles: Array<{ path: string; content: string }> };
    getSystemPrompt(): string | undefined;
    getAppendSystemPrompt(): string[];
}
```

这些 getter 只返回 `reload()` 完成后的内存快照；真正的 IO 发生在 `reload()` 里。

## 4. 各资源加载细节

### 4.1 Extension → `Extension` 对象

- **位置**：`src/core/extensions/loader.ts` 中的 `loadExtensions()`
- **加载方式**：用 `jiti` 动态 import 每个 extension 路径对应的 TS/JS 模块
- **生命周期**：
  1. 创建 `ExtensionRuntime`（共享运行时，初始时 action 方法是抛错的 stub）
  2. 为每个 extension 创建 `ExtensionAPI`
  3. 执行模块默认导出/ factory，extension 通过 `ctx.on()` / `ctx.registerCommand()` 等 API 注册能力
  4. 注册结果写入 `Extension` 对象的 Map 字段

`Extension` 类型：

```ts
export interface Extension {
    path: string;                       // 原始路径
    resolvedPath: string;               // 解析后的绝对路径
    sourceInfo: SourceInfo;
    handlers: Map<string, HandlerFn[]>; // 事件订阅
    messageRenderers: Map<string, MessageRenderer>;
    commands: Map<string, RegisteredCommand>;
    flags: Map<string, ExtensionFlag>;
    shortcuts: Map<KeyId, ExtensionShortcut>;
}
```

返回包装：

```ts
export interface LoadExtensionsResult {
    extensions: Extension[];
    errors: Array<{ path: string; error: string }>;
    runtime: ExtensionRuntime;  // 共享运行时，runner.initialize() 后才有真实实现
}
```

### 4.2 Skill → `Skill` 对象

- **位置**：`src/core/skills.ts` 中的 `loadSkills()` / `loadSkillsFromDir()`
- **发现规则**：
  - 若目录含 `SKILL.md`，整个目录视为一个 skill，不再递归
  - 否则加载该层直接子目录/文件中的 `.md`
- **元数据**：解析 markdown frontmatter

`Skill` 类型：

```ts
export interface Skill {
    name: string;
    description: string;
    filePath: string;       // SKILL.md 绝对路径
    baseDir: string;        // 所在目录
    sourceInfo: SourceInfo;
    disableModelInvocation: boolean;
}
```

注意：skill 的**正文内容不会**被解析成结构化对象，只读取 frontmatter 做索引，正文内容在使用时按需读取。

### 4.3 Prompt → `PromptTemplate` 对象

- **位置**：`src/core/prompt-templates.ts` 中的 `loadPromptTemplates()`
- **发现规则**：扫描目录下的 `.md` 文件（非递归）
- **命名**：文件名（去掉 `.md`）即为 template 名称

`PromptTemplate` 类型：

```ts
export interface PromptTemplate {
    name: string;
    description: string;
    argumentHint?: string;
    content: string;        // markdown body（去掉 frontmatter）
    sourceInfo: SourceInfo;
    filePath: string;
}
```

使用时通过 `expandPromptTemplate()` 做参数替换（`$1`, `$@`, `${1:-default}` 等）。

### 4.4 Theme → `Theme` 类实例

- **位置**：`src/modes/interactive/theme/theme.ts` 中的 `loadThemeFromPath()`
- **源文件**：`theme.json`，符合 JSON Schema `ThemeJsonSchema`
- **加载产物**：`Theme` 类的实例

`Theme` 类关键成员：

```ts
export class Theme {
    readonly name?: string;
    readonly sourcePath?: string;
    sourceInfo?: SourceInfo;

    fg(color: ThemeColor, text: string): string;
    bg(color: ThemeBg, text: string): string;
    // ... 内部把 hex / 256 色索引转成 ANSI
}
```

加载后除了返回给 `getThemes()`，还会被 TUI 通过 `setTheme()` 应用到界面。

### 4.5 Agent Context → 原始文本数组

- **位置**：`src/core/resource-loader.ts` 中的 `loadProjectContextFiles()`
- **发现规则**：从 `cwd` 向上遍历到根目录，找 `AGENTS.md` / `AGENTS.MD` / `CLAUDE.md` / `CLAUDE.MD`
- **产物**：

```ts
Array<{ path: string; content: string }>
```

直接作为文本追加到 system prompt 中，不做结构化解析。

### 4.6 System Prompt → string

`systemPrompt` 和 `appendSystemPrompt` 是两种字符串：

- `systemPrompt`：主系统提示词，优先级最高
- `appendSystemPrompt`：附加系统提示词数组，依次追加

来源可以是：
- CLI 参数 `--system-prompt` / `--append-system-prompt`
- 自动发现文件：
  - `cwd/.pi/system.md`
  - `agentDir/system.md`
  - `cwd/.pi/append-system.md`
  - `agentDir/append-system.md`

## 5. 路径到 `SourceInfo` 的溯源

资源加载层不单纯返回裸对象，还会把 `PathMetadata`（来自 package-manager 解析层）转成 `SourceInfo`，绑定到每个实例上。

主要逻辑：

```ts
// 1. package-manager resolve() 返回 ResolvedResource，含 metadata
const resolvedPaths = await this.packageManager.resolve();

// 2. reload() 里把 enabled 资源挑出来
const enabledSkills = getEnabledPaths(resolvedPaths.skills);

// 3. 加载后回填 sourceInfo
this.skills = resolvedSkills.skills.map(skill => ({
    ...skill,
    sourceInfo: findSourceInfoForPath(skill.filePath, ...)
}));
```

`SourceInfo` 字段示例：

```ts
{
    source: "npm" | "git" | "local" | "auto" | "cli";
    scope?: "user" | "project" | "temporary";
    baseDir?: string;
    packageName?: string;
    // ...
}
```

这让上层可以判断：某个 tool 来自哪个包、是否来自 npm、是否可信等。

## 6. `reload()` 的核心流程

```
1. 处理 project trust（可选）
2. settingsManager.reload()
3. packageManager.resolve() → ResolvedResource[]
4. 按 enabled 过滤
5. loadExtensions(...) → Extension[] + runtime
6. loadExtensionFactories(...) → 内联 Extension[]
7. loadSkills(...) → Skill[]
8. loadPromptTemplates(...) → PromptTemplate[]
9. loadThemes(...) → Theme[]
10. loadProjectContextFiles(...) → agentsFiles
11. 解析 systemPrompt / appendSystemPrompt
12. 冲突检测（同名 tool/command/flag）生成 diagnostics
```

所有资源都是**一次性加载进内存**，之后 runner 运行期间通过 getter 访问。

## 7. 与 Python 侧的对比要点

| 项目 | TypeScript (`coding-agent`) | Python (`nova_harness`) |
|---|---|---|
| 安装是否复制资源 | 否，保留在包安装目录 | 否，包整体保留在 `~/.nova/agent/packages/` 或项目级 `.nova/agent/packages/`，资源从包目录直接解析 |
| 资源解析入口 | `packageManager.resolve()` 读取 `settings.packages` | `PackageManager.resolve_resources()` 读取 settings + 自动发现 + 已安装包 |
| Extension 加载 | 用 `jiti` 动态 import TS/JS 模块 | 无直接等价物，目前主要是 agent/tool 配置 |
| Skill 加载 | 读取 `SKILL.md` frontmatter，生成 `Skill` 对象 | 读取 `SKILL.md`，生成对应 dataclass |
| Prompt 加载 | 读取 `.md` 生成 `PromptTemplate` | 有 prompt 模板系统，但形态不同 |
| Theme 加载 | 读取 `theme.json` 生成 `Theme` 实例 | 当前无 TUI theme 系统 |
| SourceInfo 溯源 | 每个实例都带 `SourceInfo` | manifest/resolver 也记录来源，但绑定粒度不同 |

## 8. 关键结论

- **资源加载层最终产物是强类型的内存对象**，不是路径字符串，也不是原始文件内容。
- Extension 是最复杂的资源：它会执行代码，并把注册结果填充到 `Extension` 的多个 Map 中。
- Skill / Prompt / Theme 都是**声明式资源**：加载时做 frontmatter/JSON 解析，生成只读对象供上层使用。
- 所有资源都附带 `SourceInfo`，实现「来自哪个包、是否启用、是否可信」的可追溯性。
