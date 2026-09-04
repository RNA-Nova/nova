# nova-harness

高阶 Agent SDK 与运行时宿主，构建在 [`nova-ai`](../nova_ai)（多厂商模型抽象）与 [`nova-agent`](../nova_agent)（事件驱动 Agent 框架）之上。对外提供三种形态：**SDK**（嵌入自有应用）、**CLI**（`nova-harness run` 非交互执行）、**JSON-RPC 服务器**（`nova-harness-rpc`，供终端 / Web 前端挂载），外加独立的包管理器 `nova-pkg`。

## 特性

- **AgentSession 运行时核心**：封装 `Agent`，提供会话持久化、自动重试（含上下文溢出恢复）、自动/手动上下文压缩、模型与思考级别切换、工具激活集控制、steering / follow-up 双消息队列。
- **会话树**：条目以 JSONL 落盘，支持分支（branch，可附 LLM 摘要）、fork、树内导航、会话切换/克隆/导出/导入与统计。
- **上下文压缩（Compaction）**：token 估算触发阈值判定，LLM 生成结构化摘要（支持增量更新），保留近期窗口，自动提取文件操作记录附入摘要。
- **模型运行时**：内置 provider → `models.json` → 扩展注册三层合成；credential-blind（密钥不进 `Model`，请求时解析）；动态模型目录刷新与离线缓存。
- **七类资源加载**：agents / tools / skills / extensions / prompts / user_tools / personas，统一经包分发 + user/project 两级散养目录发现，冲突诊断与来源跟踪。
- **包管理器 `nova-pkg`**：path / git / npm 三种来源的安装、卸载、更新、校验与 `[tool.nova]` 脚手架；安装事实以 `*.dist-info/` 快照记录。
- **JSON-RPC 服务器**：stdio 传输（前端以子进程挂载），76 个方法分 8 个域，多连接、事件广播、反向 UI 原语按连接寻址。
- **Project Trust**：项目级信任门控——加载 `<cwd>/.nova` 资源前的决议链、持久化记录与 UI 确认。
- **扩展系统**：事件钩子、slash 命令、快捷键、flag、provider 注册与 spawn hook；运行期动作经 `ExtensionContext` 注入。
- **UI 桥接**：`UIContext` 泛型反向原语通道（request/notify + 能力协商），交互词汇由包定义；headless 自动降级。

要求 Python `>=3.12,<3.14`。

## 目录

- [安装](#安装)
- [快速上手](#快速上手)
- [示例](#示例)
- [核心概念](#核心概念)
- [CLI 参考](#cli-参考)
- [包与扩展开发](#包与扩展开发)
- [路径约定](#路径约定)
- [开发](#开发)
- [License](#license)

## 安装

```bash
pip install nova-harness
```

`nova-ai` 与 `nova-agent` 作为依赖一并安装。运行时第三方依赖：`pydantic>=2.0`、`pyyaml`、`filelock`、`uuid6`、`pathspec`（`tomli` 仅 Python <3.11 需要）。

安装后注册三个命令：`nova-harness`、`nova-harness-rpc`、`nova-pkg`。

在 monorepo 仓库内开发时，使用根目录的 pixi 统一环境（editable 安装全部子包）：

```bash
pixi install --environment dev
```

## 快速上手

### CLI：非交互运行一个 agent

先安装一个包含 agent 组合声明的包（如官方 bundle），然后：

```bash
export VOLCENGINE_API_KEY="your-api-key"   # 或任何已配置鉴权的 provider

nova-harness run coding_agent --task "看一下当前目录结构并总结"
```

`run` 以 print 模式执行：跑完任务把最后一条助手文本打到 stdout。加 `--json` 改为输出 JSONL 事件流（首行为 session header，随后逐事件一行）。完整选项见 [CLI 参考](#nova-harness)。

### RPC：启动 JSON-RPC 服务器

```bash
# stdio——前端以子进程方式挂载，连接关闭即退出
nova-harness-rpc
```

### SDK：最小会话

```python
import asyncio

from nova_harness import create_agent_session


async def main():
    result = await create_agent_session()   # 读 ~/.nova/agent 配置，解析初始模型
    session = result.session

    await session.prompt("用一句话介绍你自己")
    await session.agent.wait_for_idle()

    print(session.get_last_assistant_text())
    session.dispose()


asyncio.run(main())
```

鉴权从环境变量（如 `VOLCENGINE_API_KEY`）、`auth.json` 已存凭据或 `models.json` 解析；初始模型按解析链确定（见[模型与鉴权](#模型与鉴权)）。会话默认持久化到 `~/.nova/agent/sessions/` 下；要临时会话可注入内存态 session manager：

```python
from nova_harness import CreateAgentSessionOptions, create_agent_session
from nova_harness.core.harness.session import SessionManager

result = await create_agent_session(
    CreateAgentSessionOptions(
        session_manager=SessionManager.in_memory("."),
        agent_name="coding_agent",      # 指定已安装包中的 agent 组合声明
        tools=["read", "grep", "find"], # 宿主级工具白名单（硬闸）
    )
)
```

需要会话切换、fork 等生命周期能力时，用 `create_agent_session_runtime()` 拿 `AgentSessionRuntime`（CLI/RPC 同款），或 `create_agent_session_by_name("coding_agent")` 直接按名启动已安装的 agent。

订阅事件做流式 UI：

```python
unsubscribe = session.subscribe(lambda event: print(event.type))
# ...
unsubscribe()
```

## 示例

[`examples/`](./examples) 目录含 3 个可直接运行的脚本（默认离线 mock 运行，真实调用在未配置 key 时自动跳过）：

- `01_quickstart.py` —— 最小会话：`SessionManager.in_memory` + `create_agent_session`，一轮 prompt，打印回复与 token 用量
- `02_events.py` —— 事件流订阅：`session.subscribe()` 打印 agent / turn / message 生命周期与运行时事件序列
- `03_extension.py` —— 最小扩展：`session_start` 钩子 + 注册 slash 命令，演示扩展发现、装载与 `bind_extensions()` 生命周期

```bash
cd packages/nova_harness
python examples/01_quickstart.py
```

## 核心概念

### AgentSession

`AgentSession` 是单一会话的运行时核心。构造依赖由 `create_agent_session*()` 工厂装配（`AgentSessionServices` 服务容器 → 扁平 `AgentSessionConfig`），常用表面按域分组：

- **消息**：`prompt(text, options)`、`steer(text)`（运行中插队）、`follow_up(text)`（收尾后续跑）、`send_user_message()`、`send_custom_message()`、`clear_queue()`、`get_steering_messages()`、`get_follow_up_messages()`；`PromptOptions` 支持 `images`、`expand_prompt_templates`、`streaming_behavior`、`source`。
- **事件**：`subscribe(listener)` 返回退订函数；事件覆盖 agent/turn/message/tool 生命周期与模型切换、队列、压缩、重试等运行时信号（`agent_start`、`turn_end`、`message_update`、`tool_execution_end`、`model_changed`、`queue_update`、`auto_retry_start`、`auto_compaction_end`、`session_replaced`、`extension_error` 等）。
- **模型**：`set_model()`、`cycle_model()`、`set_thinking_level()`、`cycle_thinking_level()`、`get_available_thinking_levels()`、`set_scoped_models()`。
- **工具**：`get_active_tool_names()`、`get_all_tools()`、`set_active_tools_by_name()`、`refresh_tools()`。
- **压缩与重试**：`compact()`、`abort_compaction()`、`set_auto_compaction_enabled()`、`set_auto_retry_enabled()`、`abort_retry()`。
- **会话生命周期**：`new_agent_session()`、`switch_agent_session()`、`fork_session()`、`clone_session()`、`export_session()`、`import_session()`、`navigate_tree()`、`list_sessions()`、`set_session_name()`、`set_label()`、`append_entry()`。
- **统计**：`get_session_stats()`（消息计数、token 分项、成本）、`get_context_usage()`（`{tokens, contextWindow, percent}`）。
- **控制**：`abort()`、`reload()`（资源与扩展重载）、`dispose()`、`trust_project()`。

状态以只读属性透出：`model`、`thinking_level`、`messages`、`system_prompt`、`session_id`、`session_file`、`is_streaming`、`is_retrying`、`is_compacting`、`pending_message_count` 等。

初始化时自动装配扩展系统：从 `ResourceLoader` 读取已加载扩展创建 `ExtensionRunner`，并把 Agent 层钩子（工具调用前后、上下文变换、payload/response 调试、turn 边界决策）绑定到 runner。`bind_extensions()` 触发 `session_start` 生命周期与扩展资源发现——`create_agent_session_runtime()` 已代劳；直接使用 `create_agent_session()` 且需要扩展完整生命周期时自行调用一次。

### 会话树与持久化

每个会话是一个 JSONL 文件，落在 `~/.nova/agent/sessions/--<cwd>--/` 下（cwd 经清洗入目录名），文件名 `<时间戳>_<session-id>.jsonl`。首行是 `type: "session"` 的 header（含文件版本 `3`），其后逐行一个条目，条目类型：

| `type` | 含义 |
|--------|------|
| `message` | LLM 消息（user/assistant/toolResult 与自定义消息） |
| `thinking_level_change` / `model_change` | 思考级别 / 模型切换记录 |
| `compaction` | 一次压缩的产物（摘要 + 保留边界） |
| `branch_summary` | 分支摘要（导航时生成） |
| `label` | 条目标签 |
| `session_info` | 会话命名 |
| `custom` / `custom_message` | 扩展与包写入的自定义条目 / 消息 |

条目带 `parent_id` 构成树：**branch** 在同文件内把 leaf 指针移回历史条目（可先生成分支摘要），**fork** 复制出一个新会话文件从指定条目继续，`navigate_tree()` 在树内移动 leaf 指针并可选生成摘要。`reload()` / 重启进程后从 JSONL 逐行回放恢复上下文；会话中记录的模型与思考级别随回放一并恢复。

### 上下文压缩

压缩设置（settings `compaction` 段，均为默认值）：

```json
{
  "compaction": { "enabled": true, "reserve_tokens": 16384, "keep_recent_tokens": 20000 }
}
```

- **触发判定**：估算上下文 token（字符数启发式）超过 `context_window - reserve_tokens` 即应压缩。自动压缩在每次请求前检查；上下文溢出类错误会触发一次"压缩 + 重试"的溢出恢复。
- **手动压缩**：`session.compact()`（可传自定义指令），`abort_compaction()` 取消。
- **压缩方式**：从会话条目中按 `keep_recent_tokens` 预算找切点，切点之前的历史交给 LLM 生成结构化摘要（已在摘要覆盖范围内的部分做增量更新），并提取压缩区间内的文件操作（读/改）清单附入摘要；压缩结果以 `compaction` 条目持久化，回放时以摘要 + 近期窗口重建上下文。
- **分支摘要**走同一引擎（settings `branch_summary.reserve_tokens`，默认 16384），用于会话树导航与 fork 前的上下文交接。

### 自动重试

可重试错误（网络错误、限流、5xx 等）按指数退避自动重试，事件流透出 `auto_retry_start` / `auto_retry_end`。settings `retry` 段（默认值）：`enabled: true`、`max_retries: 3`、`base_delay_ms: 2000`、`max_delay_ms: 60000`。`set_auto_retry_enabled(False)` 关闭，`abort_retry()` 放弃当前等待中的重试。

### 模型与鉴权

`ModelRuntime` 是模型与鉴权的运行时门面（`session.model_runtime`）：

- **三层合成**：内置 provider（来自 `nova-ai`）→ `~/.nova/agent/models.json` 覆盖/新增 → 扩展 `registerProvider()` 注册。无覆盖时内置 provider 原样进入集合。
- **credential-blind**：`api_key` 与 `Authorization` 头不写入 `Model`；每次请求经鉴权链解析（运行时覆盖 → 已存 credential → `models.json`/扩展配置的 key → 环境变量 → OAuth 刷新）。
- **动态目录**：`refresh()` 走网络刷新已配置 provider 的模型列表；启动时以 15 秒预算尝试一次。`NOVA_OFFLINE` 置位时只读 `models-store.json` 缓存。
- **凭据管理**：`login()` / `logout()` / `check_auth()` 直通 `nova-ai`；`set_runtime_api_key()` / `remove_runtime_api_key()` / `list_credentials()` 管理进程级覆盖。

初始模型解析链（优先级从高到低）：调用方显式 `model` → CLI `--provider/--model` → scoped models 首个（新会话）→ agent 组合声明 yaml 的 `model:` 字段 → settings 默认模型 → 任一有鉴权可用模型。继续/恢复会话时优先恢复会话中保存的模型。

`models.json` / `auth.json` 中的值支持引用解析：`$VAR` / `${VAR}` 取环境变量（缺失即报错并指明变量名），`!cmd` 前缀执行 shell 命令取输出，`$$` / `$!` 为转义，其余按字面量。

### Settings

双层设置：**全局** `~/.nova/agent/settings.json` 与**项目级** `<cwd>/.nova/settings.json`（项目级受 Project Trust 门控），字段级合并。`SettingsManager` 是唯一写门：所有写入经类型化 setter + 字段级 dirty 追踪，后台写队列落盘（`flush()` 强制冲刷）。

运行时消费的主要键：`default_provider` / `default_model` / `default_thinking_level`、`steering_mode` / `follow_up_mode`（`all` / `one-at-a-time`）、`compaction`、`branch_summary`、`retry`、`shell_path`、`shell_command_prefix`、`default_project_trust`、`packages`（已配置包源清单）、`extensions` / `skills` / `prompts` / `agents` / `personas`（显式资源路径）、`tools` / `user_tools`（名字 pattern 开关）、`role_boundary`（`open` / `strict`）、`disabled_commands`、`enabled_models`、`http_idle_timeout_ms`。其余展示偏好键（theme、editor 等）由前端消费，运行时只负责存储与 round-trip，不解释语义。

### 资源加载

七类资源，三个来源，统一由 `DefaultResourceLoader` 装配（优先级从高到低）：

1. **project settings 显式条目**与 **project 自动发现**（`<cwd>/.nova/`，受 trust 门控）
2. **user settings 显式条目**与 **user 自动发现**（`~/.nova/agent/`）
3. **package 贡献**（`nova-pkg` 安装的包，经 `[tool.nova]` 声明或包内约定目录扫描）

自动发现的目录约定（散养资源）：`<base>/backend/extensions`、`backend/skills`、`backend/prompts`、`backend/personas`，以及两半共享平级的 `<base>/agents`（base 即 `~/.nova/agent` 或 `<cwd>/.nova`）。skills 另有 `.agents/skills` 通道：user 级收 `~/.agents/skills`，project 级从 cwd 向上收集到 git root 为止。**tools 与 user_tools 不做顶层自动发现**——只来自已安装包。

同名冲突按优先级遮蔽并记录诊断（`ResourceLoader.get_diagnostics()`）；资源类型、加载产物与诊断见 `core/types/resources/`。上下文文件（`AGENTS.md` 等）不属于包资源，从 cwd 向上遍历发现，见 `core/resources/loaders/context_files.py`。

### Project Trust

项目级资源（`<cwd>/.nova/` 下的 settings、extensions、skills、prompts、`SYSTEM.md`、`APPEND_SYSTEM.md`，以及项目祖先目录的 `.agents/skills`）在加载前需要信任决议。决议链：

1. **显式覆盖**：CLI `--trust` 或 SDK `project_trusted` 参数；
2. **无待门控资源**：目录里没有任何上述资源即视为可信；
3. **扩展裁决**：`project_trust` 事件允许扩展给出决定（可选记忆）；
4. **持久化记录**：`~/.nova/agent/trust.json` 中已存的逐路径决定；
5. **默认策略**：settings `default_project_trust`（`always` / `never`）；
6. **无 UI 默认不信任**；有 UI 时弹出选择框（Trust / Trust parent folder / 仅本会话 / Do not trust），选择可持久化。

信任只存在于运行时（会话启动决议 + resolver 读取门控 + `trust.json` 记录）；`nova-pkg` 的装/卸是用户主动行为，不做 trust 检查。会话运行中可经 `session.trust_project()` 翻转——持久化 `trust.json` 并在进程内即时生效（后续资源重载按新裁决读取项目资源）。

### 扩展系统

扩展是单文件 `<name>.py`，或含 `extension.py` / `__init__.py` 的目录，暴露名为 `extension` 或 `load` 的工厂函数，装载期收到 `NovaExtensionAPI` 做声明式注册：

```python
def extension(nova):
    nova.on("session_start", on_session_start)          # 事件订阅，返回退订函数
    nova.registerCommand("hello", {                      # /hello slash 命令
        "description": "打招呼",
        "handler": on_hello,
    })
    nova.registerFlag("verbose", {"type": "boolean", "default": False})
    nova.registerShortcut("ctrl+alt+h", {"description": "…", "handler": on_shortcut})
    nova.registerProvider("my-provider", config)         # 自定义模型 provider
    # nova.events：扩展间共享的事件总线；nova.getFlag(name) 读 flag 值
```

事件 handler 收到 `(event, ctx)`；运行期动作统一走 `ctx`（`ExtensionContext`）：`send_message` / `send_user_message`、`append_entry`、`exec()`、`set_active_tools()` / `get_all_tools()`、`set_model()`、`compact()`、`abort()`、`get_context_usage()`、`is_project_trusted()`、`shutdown()`，以及 `ui` / `has_ui`（见下节）、`cwd`、`session_manager`、`model_runtime` 与 persona/agent 管理动作。命令 handler 额外收到注入会话生命周期动作（`new_session` / `fork` / `switch_session` / `reload` / `export` / `import_session` 等）的扩展 ctx。

可订阅事件包括会话生命周期（`session_start` / `session_shutdown` / `session_before_switch` / `session_before_fork` / `session_before_compact` / `session_compact` / `session_before_tree` / `session_tree`）、交互钩子（`tool_call` / `tool_result` / `context` / `input` / `user_bash` / `before_agent_start` / `before_provider_request` / `after_provider_response` / `prepare_next_turn` / `should_stop_after_turn`）、`resources_discover`（贡献临时资源路径）、`project_trust`（信任裁决）与底层 `AgentEvent` 桥接。

扩展来源：包（`[tool.nova] extensions`）、`~/.nova/agent/backend/extensions/`、`<cwd>/.nova/backend/extensions/`（trust 门控）、settings `extensions` 显式路径；agent yaml 可按名白名单过滤。`session.reload()` 关闭当前 runner 并重载扩展（保留 flag 值）。

### UI 桥接

`UIContext`（`core/types/ui/context.py`）是后端到前端的**反向原语通道**，泛型 transport、零交互词汇，只有四个方法：

```python
class UIContext(ABC):
    @property
    def capabilities(self) -> Set[str]: ...          # 前端声明支持的原语集合
    def has_capability(self, method: str) -> bool: ...
    async def request(self, method: str, params: Dict[str, Any]) -> UIResponse: ...  # 需响应
    def notify(self, method: str, params: Dict[str, Any]) -> None: ...               # fire-and-forget
```

- 交互词汇（select / confirm / input / 对话框等）由**包**定义并自定义 params，harness 不内置；前端按能力上报，未支持的方法优雅降级。
- 无 UI 的运行模式（print / headless）注入 `NoOpUIContext`，全部安全 no-op；消费方先判 `has_ui` / `has_capability` 再决定走不走交互路径。
- RPC 模式下通道为 `ui/request` / `ui/response` 配对 + `system/capabilities` 上报，按连接寻址（发起方连接优先，无归属广播首响应胜出）。
- 工具执行期的 UI 句柄经 `ToolExecContext.ui` / `has_ui` 注入（弹窗串行锁与 abort 竞速已由框架织入）；扩展经 `ctx.ui` 使用同一通道。

## CLI 参考

pip 安装注册三个命令：`nova-harness`、`nova-harness-rpc`、`nova-pkg`。打包（二进制）形态下三者合并为统一入口 `nova-server [rpc|run|pkg]`（裸跑缺省 rpc——TUI 以子进程挂载；`run` 为 print 一次性执行，子代理自调走这里；`pkg` 为包管理器），挂在同一批 main 函数上。

### `nova-harness`

```
nova-harness [--version]
nova-harness run <agent> --task TASK [选项]
```

`run` 子命令以 print 模式非交互执行一次任务（`agent` 与 `--task` 均必填，缺失返回退出码 2）：

| 选项 | 说明 |
|------|------|
| `--task TASK` | 交给 agent 的任务文本 |
| `--cwd DIR` | 工作目录（默认当前目录） |
| `--json` | 输出 JSONL 事件流（默认输出最终助手文本） |
| `--trust` | 信任当前项目目录（加载 `.nova` 设置与资源） |
| `--no-session` | 不持久化会话（内存态，跑完即弃） |
| `--skill PATH` | 本次运行临时加载的 skill（可重复，不持久化） |
| `--prompt-template PATH` | 本次运行临时加载的提示词模板（可重复） |
| `--tools/-t NAMES` | 逗号分隔的工具白名单（宿主级硬闸） |
| `--exclude-tools/-xt NAMES` | 逗号分隔的工具黑名单（在 `--tools` 之后应用） |

退出时清理被跟踪的后台子进程，不留孤儿。

### `nova-harness-rpc`

```
nova-harness-rpc [--version]
```

stdio 单客户端形态：前端以子进程挂载，连接关闭即进程退出。
- 方法表：76 个方法分 8 个域——`session`（initialize/shutdown/会话/队列/重试/压缩/树导航/导入导出）、`model`（发现/切换/scoped/思考级别）、`auth`（状态/login/logout/setApiKey）、`resources`（skills/提示词模板目录）、`settings`（读写，无需会话）、`user_tools`、`system`（命令目录/扩展 flag/快捷键目录与回调）、`package`（列表/安装/卸载/更新/检查）。`ui/response` 与 `system/capabilities` 由服务器按连接直管。
- 诊断：进程 stderr 重定向到 `~/.nova/agent/logs/rpc-stderr.log`（附加写）；stdout 有 OutputGuard 保护，杂散输出不会污染协议帧。

### `nova-pkg`

```
nova-pkg list [--flat] [--configured]
nova-pkg install [source] [--no-deps] [--dry-run] [--editable]
nova-pkg uninstall <name_or_source>
nova-pkg update [name_or_source]      # 省略则更新全部可更新的已配置包
nova-pkg info <name_or_source>
nova-pkg validate <source>
nova-pkg init [directory] [--name NAME]
```

通用旗标：`--local/-l` 操作项目级存储（`<cwd>/.nova`，默认 user 级 `~/.nova/agent`）；`--json` 机器可读输出。

source 规范：

| 形态 | 示例 |
|------|------|
| 隐式 / 显式路径 | `./my-pkg`、`/abs/path`、`path:./my-pkg` |
| editable 路径 | `path:./my-pkg` + `--editable`（原地引用不复制） |
| git | `git:github.com/user/repo@main`、`git:git@github.com:user/repo.git@v1.0` |
| https | `https://github.com/user/repo@ref`（自动识别为 git 源） |
| npm | `npm:<name>[@<version-or-range>]`（支持精确版本、`^`/`~`、x-range、比较器集、`||` 并集与 hyphen range，省略为 latest） |

安装行为：

- 包整体复制（或 editable 引用）到 `<store>/packages/{path,git,npm}/`；安装事实写入 sibling `<name>.dist-info/`（`direct_url.json` PEP 610、`package_name`、`installed_at`），之后只读。
- Python 依赖按 `pyproject.toml`（Poetry / PEP 621）与 `requirements.txt` 解析，经 `uv pip`（存在时）或 `python -m pip` 安装进当前环境；`--no-deps` 跳过。包自身是可安装 Python 包（声明 `name` 且有 build-system）时以 `--no-deps` 自安装，供工具/扩展 import 包内共享模块。
- 安装后 source 记入 settings `packages` 清单；会话启动时发现已配置但缺失的包会自动补装（进度经回调/`package_progress` 通知透出）。
- `uninstall` 同时移除 settings 记录与（如有）已自安装的 Python 包；被其他包 `requires` 引用的包拒绝卸载。

## 包与扩展开发

### 包形态与 `[tool.nova]` 清单

**A 型包**（Python）：包根 `pyproject.toml` 承载身份（`[tool.poetry]` 或 PEP 621 `[project]`）与 Nova 清单：

```toml
[tool.poetry]
name = "my-nova-package"
version = "1.0.0"
description = "..."
authors = ["you"]

[tool.nova]
agents     = ["./agents/"]                 # 目录：扫描其下 *.yaml 组合声明
tools      = ["./tools/read.py", "./tools/bash.py"]
extensions = ["./extensions/my_ext.py"]
skills     = ["./skills/"]
prompts    = ["./prompts/"]
user_tools = ["./user_tools/bash.py"]
personas   = ["./personas/"]               # 目录条目，递归收 .md
auto_install_dependencies = true
requires = ["other-nova-package"]
```

- 七类资源类目均可省略；**显式清单优先于包内约定目录扫描**，显式空列表 `[]` 表示该类目不提供。
- **B 型包**（纯 TS/前端资产）：无 `pyproject.toml`，以包根 `package.json` 为身份证，Nova 段放顶层 `"nova": {"requires": [...]}`（B 型无 Python 能力类目可声明）。
- `nova-pkg init` 按当前目录结构自动生成 `[tool.nova]` 段；`nova-pkg validate` 校验包源合法性。

### Agent 组合声明（`agents/<name>.yaml`）

一文件一 agent，纯组合声明（元数据 + 能力名单），示例：

```yaml
name: my_agent            # 缺省 = 文件名
description: ...
model: volcengine/doubao-seed-2-0-mini-260428   # 人格默认模型（可选）
persona:                  # 人格条目：相对本文件的路径（收敛包根内）或注册名
  - ../backend/personas/core.md
tools: [read, grep, bash] # 能力名单统一三态：键缺席=全放，[]=全禁，名单支持 ! 排除
extensions: []            # tools / extensions / user_tools / commands / skills 同规则
```

名单字段（`tools` / `extensions` / `user_tools` / `commands` / `skills`）语义：键缺席 = 全放；显式 `[]` = 全禁；非空 = 名单（支持 `!` 排除）。`skills` 名单只约束包内 skill（用户级/项目级始终放行）。`tools` 另有 settings `role_boundary` 语义开关：`open`（默认）只做初始激活集，`strict` 裁剪注册表。

### 工具（LLM 工具）

形态：`tools/<name>.py` 单文件（推荐）或 `tools/<name>/executor.py` 目录（需同目录资产时），暴露 `Tool` 类，元数据即类属性：

```python
class Tool:
    name = "weather"                       # 必需
    description = "查询天气"                # 必需
    parameters = {                         # 必需，JSON Schema
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    }
    label = "Weather"                      # 可选：展示名
    execution_mode = "sequential"          # 可选：并行/串行
    prompt_snippet = "..."                 # 可选：系统提示词补充
    prompt_guidelines = ["..."]            # 可选：使用准则

    def __init__(self, context):
        # context: ToolContext——cwd（值）+ settings（只读活视图），构造期不变量
        self.cwd = context.cwd

    async def execute(self, tool_call_id, params, signal, on_update, ctx):
        # params 已过 JSON Schema 校验；signal 取消；on_update 进度回调；
        # ctx: ToolExecContext——model / ui / has_ui / agents（每次调用现取注入）
        ...
```

可选类属性 `prepare_arguments` 在调用前转换参数。执行期需要交互时用 `ctx.ui`（先判 `ctx.has_ui`）；委派类工具用 `ctx.agents` 注册表快照按名查表。工具结果可携带 `details`（任意 JSON 值）供前端渲染。

### 用户工具（User Tool）

用户/前端触发、结果以自定义消息注入 LLM 上下文的宿主能力。形态同工具（`user_tools/<name>.py` 或 `<name>/executor.py`），暴露 `UserTool` 类：

```python
class UserTool:
    name = "bash"                          # 必需
    description = "执行 shell 命令"         # 必需
    parameters = {...}                     # 必需，JSON Schema（注册表只透传）
    MESSAGE_TYPES = [MyMessage]            # 可选：加载时注册进消息回载注册表

    def __init__(self, session): ...       # 构造注入会话上下文

    async def execute(self, params, on_event, signal):
        # on_event(name, data) 进度事件透出前端；返回 CustomAgentMessage
        # （应实现 ContextInjectable 协议以进入 LLM 上下文）
        ...

    def message_from_result(self, params, result): ...  # 可选：扩展拦截结果转消息
```

### 扩展

单文件 `extensions/<name>.py`（推荐）或目录形态（`<name>/extension.py` / `__init__.py`），工厂函数名为 `extension` 或 `load`，契约见[扩展系统](#扩展系统)。扩展必须是自包含的；agents、skills、prompts、personas 同理不依赖包的 Python 模块。

### 二进制依赖

性能加速用的可选二进制，三档声明（均在 `[tool.nova]`）：

- `binary_dependencies = { rg = "ripgrep" }`——wheel 可装的二进制，安装时随 pip 依赖进入环境 `bin/`；
- `binary_managed_dependencies = ["fd"]`——框架注册表自管理（pin 版本 + sha256，下载到 `~/.nova/agent/bin/`；注册表只收 PyPI 覆盖不了的必需项，当前仅 `fd`；`NOVA_OFFLINE` 跳过下载仅警告）；
- `binary_system_dependencies = ["docker"]`——无自动安装渠道的系统要求，安装时校验存在性、缺失仅警告。

运行时经 `resolve_binary()` 三级解析（环境 `bin/` → `~/.nova/agent/bin/` → PATH）。工具应按"二进制加速 + 纯 Python 兜底"设计，缺失不影响可用性。

### 包间依赖（`requires`）

`requires = ["<包名>"]` 声明对**其他 Nova 包**的依赖（非 Python/npm 依赖）：安装时校验被依赖包已安装（user/project 合并视图，任一 scope 命中即满足），缺失即拒绝并附提示；卸载时被引用的包拒绝卸载。v1 只做约束校验，不做来源解析（无中心 registry）。

## 路径约定

全局配置根 `~/.nova/agent/`（环境变量 `NOVA_AGENT_DIR` 可覆盖；`NOVA_APP_NAME` / `NOVA_CONFIG_DIR` 控制品牌名与配置目录名）：

```
~/.nova/agent/
├── settings.json          # 全局设置
├── auth.json              # 已存凭据（AuthStorage 管理）
├── models.json            # 自定义 provider / 模型覆盖
├── models-store.json      # 动态模型目录缓存
├── trust.json             # Project Trust 持久化记录
├── agents/                # user 级 agent 组合声明（*.yaml）
├── backend/               # user 级散养资源
│   ├── extensions/  skills/  prompts/  personas/
├── packages/              # 已安装包（path/ git/ npm/ 三个子目录族）
├── sessions/--<cwd>--/    # 会话 JSONL
├── bin/                   # 框架自管理二进制（fd）
└── logs/rpc-stderr.log    # RPC 进程 stderr 日志
```

项目级（受 Project Trust 门控）：`<cwd>/.nova/` 同构——`settings.json`、`agents/`、`backend/{extensions,skills,prompts,personas}`、`packages/`。skills 另有 `~/.agents/skills` 与项目祖先 `.agents/skills` 通道。

会话 JSONL 为明文存储，可能包含敏感代码片段或输出；`auth.json` 含密钥，注意权限。

## 开发

```bash
# monorepo 根目录：安装 dev 环境后跑本包测试（排除真实 API 集成测试）
pixi run -e dev test-harness

# 或在子包内直接跑
cd packages/nova_harness
pixi run -e dev pytest tests -m "not integration"

# 真实 API 集成测试（tests/integration/ 与 tests/smoke/ 中带标记用例，
# 需 VOLCENGINE_API_KEY 等环境变量）
pixi run -e dev pytest tests -m integration
```

## License

MIT
