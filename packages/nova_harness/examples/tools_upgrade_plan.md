# Nova 工具框架升级（ToolContext 与单文件形态）——已落地

> 本文档记录 tools 框架升级的最终设计与实现状态（2026-07-27 完成）。
> 分析过程见 `pi_boundary_map.md`（pi 全边界的对照基线）。

## 1. 升级动机

拆分 tools/ 类目后，包工具处于"形态完整、供养不足"状态：

- **上下文盲区**：`Tool` 构造无参、execute 仅 4 参——bash 拿不到
  settings 的 shell 配置、read 拿不到当前模型、文件工具隐式依赖进程 cwd；
- **元数据面窄**：类属性只有 name/description/parameters/prompt_*，
  窄于框架自身的 ToolDefinition；
- **形态仪式化**：一个工具一个目录，目录里永远只有 executor.py 一个文件。

## 2. 终版设计

### 2.1 ToolContext / ToolExecContext（`core/types/resources/tools.py`）

pi 的工具供养是两条线（构造闭包 `createXTool(cwd, options)` + execute 第 5 参
ExtensionContext），Nova 收敛为**同一条注入管线的两个时点**——不变量构造期给、
会话可变状态调用期给：

```python
@dataclass
class ToolContext:                    # __init__(context)：构造期，不变量
    cwd: str                          # 会话级不变 → 值
    settings: ToolSettingsView        # 会 reload → 活视图

@dataclass(frozen=True)
class ToolExecContext:                # execute 第 5 参：每次调用现造
    model: Optional[Model] = None     # 会切换 → 调用时刻现取（无访问器、无后绑定）
```

- **"不变量给值，可变量给访问器"**——时效性差异体现在注入点，不在通道；
- `ToolSettingsView` 是结构化 Protocol（SettingsManager 天然满足，
  包工具拿不到写方法）；
- 构造期 `ToolContext`（`cwd` 值 + `settings` 活视图）只装不变量；
  执行期 `ToolExecContext`（冻结值对象，当前为 `model`）作为 `execute`
  第 5 参由 `DynamicTool` 经 `context_provider` 每次调用现取注入
  （对齐 pi `wrapToolDefinition` 的 `ctxFactory`）——无后绑定、无 stub；
- **loader 只产出 `ToolDefinition`**（对齐 pi"加载层定义、会话层包装"），
  包装与 provider 注入统一在 `ToolsManager.refresh` 单点完成；
- **增长纪律**：新的会话能力只在 `ToolExecContext` 加字段（有具名消费者才加），
  永不开第二条注入通道；
- 两梯度上下文：包 LLM 工具 `__init__(ToolContext)` + `execute(..., ctx)`
  < 用户工具 `__init__(session)`（工具永远不走扩展，无第三梯度）。

### 2.2 单文件形态

```
tools/
├── bash.py          ← 单文件即工具（推荐，对齐 pi 的 tools/bash.ts）
└── complex_tool/    ← 目录形态保留：需同目录资产时使用
    └── executor.py
```

- `ToolLoader` / `UserToolLoader` / `validation.py` / `resolve/discovery.py`
  全部支持双形态；元数据契约不变（类属性，免构造可读）；
- `Tool` 类属性补齐 `label` / `execution_mode` / `prepare_arguments`，
  与框架 ToolDefinition 同宽；
- bundle 8 个工具 + user_tools/bash 已全部迁移单文件。

### 2.3 spawn hook 抬到 process 层

框架不认识具体工具，但"启动子进程"是合法 OS 抽象：
`types/extensions/process.py`（`SpawnContext`/`SpawnHook`/`SpawnHookAware`），
扩展 API `registerSpawnHook`；任何 spawn 类工具实现协议即可接入
（bash 是第一个消费者）。pi 的 spawnHook 仅是 bash 工具构造选项、扩展不可达，
Nova 这条缝是超集。

## 3. 落地清单

| 项 | 状态 |
|---|---|
| ToolContext（构造期不变量）全链接线（loader options → ToolLoader → executor） | ✅ |
| ToolExecContext + context_provider（execute 第 5 参，对齐 pi ctxFactory） | ✅ |
| loader 契约改产出 ToolDefinition、包装收拢 ToolsManager.refresh 单点 | ✅ |
| settings 活视图（shell_path / shell_command_prefix / image_auto_resize） | ✅（字段与 getter 本已存在，此前无人消费） |
| 类属性补齐 label / execution_mode / prepare_arguments | ✅ |
| 单文件形态（loader / validation / discovery / scaffold 全链路） | ✅ |
| bundle 迁移 + bash shell 配置消费 + read 视觉检查/auto_resize | ✅ |
| spawn hook → process 层（registerSpawnHook / spawn_hooks） | ✅ |

## 4. 顺带修复的潜伏 bug

1. **read 图片路径必然失败**：`ImageContent.data` 需要 base64 字符串，
   此前直接传 bytes（该路径无真实图片测试）——已修并补视觉/非视觉两侧测试；
2. **subagent 项目级路径**：`.nova/agent/agents` → `.nova/agents`
   （`.nova/agent/` 中缀只属于全局目录）；
3. **`load_agents` 不存在**：runner.py 延迟导入的函数在 agent_config.py 中
   从未实现（调用即 ImportError）——已补实现 + scope 语义回归测试。

## 5. 明确不做 / 挂起

- `registerTool`（扩展动态注册工具）：**明确不做**——工具永远只走包管线
  （`[tool.nova] tools` 一条线），扩展层不碰工具；pi 把它挂在扩展通道上的
  `addedToolNames` 当轮回报随之不做。工具将来需要的会话能力（发消息等）
  只在 `ToolExecContext` 加字段，经既有的 execute 第 5 参通道到达；
- pi 的 per-tool 类型化 options（BashToolOptions 等）：框架零内置的固有成本，
  由 ToolContext 通用载体替代；
- pi 的 operations 执行后端替换：Nova bash 引擎本地执行已够，不暴露；
- `agent_settled` / `before_provider_headers` 事件缺口：独立小活，另行接线；
- 元数据文件（YAML/JSON）不回：代码元数据可插值代码常量（截断上限等），
  市场索引走构建期生成 manifest（dist-info 模式）。
