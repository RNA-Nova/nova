# nova-executor 接入与环境感知注入设计（v1）

> 本文是 executor 接入的完整设计定案。经多轮讨论 + codex 实证对照
> （codex-rs `tools/orchestrator.rs`、`parallel.rs`、`context-fragments/`、
> `world_state/`、`hooks/`）形成。状态：**设计定案，实施中**。
>
> 阅读对象：没参与讨论的工程师。读完应能回答"做什么、为什么这么做、
> 为什么不是别的做法、什么留着没做"。

---

## 一、背景与动机

Nova 的 bash 工具当前只在本地直接 spawn 子进程，无沙箱、无远程能力。
`nova_executor`（Rust 通用执行后端）已完成清洗（纯 process/fs/pty +
三平台沙箱 + PROTOCOL.md v1.0），但未被 Nova 消费。本文把它接成
Nova 工具链的执行后端，并让模型对当前执行环境有准确感知。

**目标用户场景**：本地开发（沙箱隔离）+ 远程算力（GPU 服务器跑训练/
分析——环境即工作区，不做文件同步）。

## 二、前置（已完成）

- executor 通用执行后端清洗（`packages/nova_executor`：删除 Codex agent
  层，纯执行后端；`nova-executor-client` 回归薄客户端）；
- 工具并行门控（`nova_agent/agent_loop/execution_gate.py`：公平 FIFO
  读写门，sequential 工具不再毒化整批——codex `parallel.rs` 对位）。

## 三、架构决策（判决记录）

### R1：执行后端切换是用户主动的（`/executor` slash 命令）

- **判决**：v1 只有用户能切（`/executor` 选择器 / `/executor local` /
  `/executor remote <name|url>`）；模型不能切。
- **理由**：后端切换是安全敏感的控制面决策（数据出不出本机），先握在
  人手里。模型主动申请（codex `request_permissions` 对位）留作挂账。
- **载体是 slash 不是 user tool**：切换是会话控制面动作，不是工具调用。

### R2：切换的三通道分离（记忆/执行/回执）

`/executor` 切换一次，三个通道各管一件事：

| 通道 | 管什么 | 形态 |
|---|---|---|
| 会话条目 | **记忆**（分支切换/会话恢复时还原选择） | `executor` 条目 + `session_start`/`session_tree` 钩子恢复（tools_panel/subagent_gate 先例） |
| runtime cell | **执行**（bash 引擎执行期直读，立即生效） | bundle 侧 `nova_coding_agent/executor/runtime.py`（`set_backend_selection`——扩展与引擎同包直调，不需核心 action）；`refresh_system_prompt` action 负责切换后重建系统提示词 |
| notice 条目 | **用户回执**（转录里看到"已切换到 X"） | `store.addNotice`——**用户可见但不进模型上下文** |

**被否方案**：切换当刻注入"即时片段"进模型上下文——违反"回执与感知
分离"（codex 实证：无即时注入通道，环境变化统一下一采样步对账捕获）。
切换通知混进上下文会污染模型的环境认知（过去式陈述 vs 当前真值）。

### R3：工具感知不经上下文，直读真值

模型的环境感知（系统提示词环境段）和工具的执行后端（runtime cell）
**同源两路**：都由切换命令翻转同一真值。工具永不从上下文读环境——
片段/提示词是给模型"知道"的，执行是"真值"的。codex 同款
（world state 片段给模型看，工具经 step context 直读 Environment）。

### R4：环境感知走系统提示词，不建片段通道（本期最重要的取舍）

**判决**：环境信息进系统提示词的"环境段"（`# Environment` +
`<environment>` 标记块），切换/权限变化时经 `_sync_system_prompt`
重建（tools_panel 同款先例）。**不建** codex 式片段通道
（world_state diff 门控）。

**理由**：

1. **缓存几何学只对高频成立**：系统提示词在第 0 位，变了前缀缓存全
   失效——但 executor 切换是会话里屈指可数的低频动作，重建代价可
   接受（tools_panel 每次切换本来就这么付）。为低频需求建 diff 门控
   通道是过度工程。
2. **developer 角色不被全厂商支持**：codex 片段双角色（环境=user、
   指令=developer），而 kimi 的 compat 目录里 `supports_developer_role=
   False`——通道的语义先打折。system 角色全厂商通吃，零兼容分支。
3. **没有消费者不建**：片段通道的第一个高频消费者（如 per-turn token
   预算提醒）还不存在。

**什么时候回摆**：出现"每轮/高频要刷新的上下文"那天——系统提示词
重建的缓存代价不可接受时，片段通道转正（完整设计已定案，见 §六挂账）。

### R5：内容清单六项（codex 实证）

环境段内容照 codex `environment_context` 实证形态：

```
# Environment

<environment>
  <backend>local | executor-local | executor-remote</backend>
  <environment_id>gpu-01</environment_id>      <!-- 远程时有 -->
  <cwd>file:///home/user/project</cwd>         <!-- file:// URI，本地远程同形 -->
  <shell>bash</shell>                          <!-- shell 即平台代理，不写 OS 字段 -->
  <filesystem>
    <workspace_roots><root>…</root></workspace_roots>
    <permission>read-only | workspace-write | full</permission>
  </filesystem>
  <network>unmanaged | managed（allowed 域名清单）</network>
  <current_date>2026-08-19</current_date>
  <timezone>Asia/Shanghai</timezone>
</environment>
```

逐项理由：

- **shell 不写独立 OS 字段**：codex 实证——`shell=powershell` 即 Windows，
  `bash/sh` 即 POSIX，模型看 shell 写对平台命令；
- **cwd 用 `file://` URI**：同一字段形态本地远程通吃，路径解释归 executor
  主机规则（codex PathUri 对位）；
- **filesystem 权限档**：映射我们的 plan_mode / role_boundary 体系——模型
  知道写哪里不会被拦、何时先请示；
- **network**：能不能联网决定 pip/curl 类命令写法。

**纪律两条**：纯声明数据（无祈使句——防带偏首条用户消息，codex/
pi/Claude Code 的标记块训练分布实证安全）；`<environment>` 标记包裹
（将来搬片段通道时认得出来、搬得走）。

### R6：cwd 搬家 + 划界规则

- cwd、timestamp 从系统提示词 Meta 动态段**搬进环境段**（它们随后端
  切换变）；session_id、custom_vars 留 Meta（会话内不变）；
- 划界规则一句话：**会话中途会变的 → 环境段（重建刷新）；会话内不变
  的 → 系统提示词静态段**（persona/工具指南/skills 索引/委派菜单/项目
  指令不动）。

## 四、改动清单

### bundle（nova_coding_agent）——主战场

| 改什么 | 在哪 |
|---|---|
| 新增 `ExecutorBashOperations`（executor 后端实现，经 nova-executor-client 薄客户端） | `backend/nova_coding_agent/bash/`（`BashOperations` Protocol 本就定义在此） |
| bash 引擎改执行期解析后端（构造期定型 → 执行期读 runtime cell） | `backend/tools/bash.py` + `bash/engine.py` |
| executor 客户端生命周期 + 本地 spawn（二进制解析链：env → nova bin → 本地构建路径 → PATH） | `backend/nova_coding_agent/executor/`（新模块） |
| 新增 `executor_switch` 扩展（/executor 命令 + 条目持久化 + notice + 触发环境段重建） | `backend/extensions/executor_switch.py` |

### 核心（nova_harness）——三处小加法

| 改什么 | 在哪 |
|---|---|
| settings 加 `executor` 字段（`ExecutorSettings`：默认后端 + 已知端点清单；**token 归 auth.json 不进 settings**） | `core/types/config/settings.py` + `manager.py` 读取方法；线上 schema 再生成 + 漂移测试跟进 |
| 二进制注册表加 `nova-executor` 条目（或走 `binary_dependencies` PyPI wheel 车道——ripgrep 先例，pip 即发布渠道） | `package/binaries/registry.json` / bundle pyproject |
| ExtensionContext 加 `get_executor_settings`（端点清单数据源）与 `refresh_system_prompt`（切换后重建触发）两个 action | `core/types/extensions/context.py` + `actions.py` + `runner.py` + `agent.py` 接线 |

### nova_agent——本期不动

片段插槽（`assemble_fragments`）挂账到片段通道落地时——本期无循环改动。

### 不动的

- `nova_executor`（Rust）：已清洗完，零改动；
- `nova-executor-client`：薄客户端已就位，零改动；
- 前端（nova-client/TUI）：notice 渲染是既有能力，零改动。

## 五、验收

- 各包测试全绿 + 新增：ExecutorBashOperations 双后端用例、/executor
  切换的条目持久化与恢复、环境段渲染（本地/远程两形态）、settings
  executor 字段 round-trip；
- 端到端：`/executor local` 沙箱跑 bash → `/executor remote` 切换 →
  问模型"你在什么环境"答得对（感知验证）。

## 六、挂账（已定案、等触发条件）

| 挂账项 | 触发条件 | 定案内容 |
|---|---|---|
| **片段通道**（ContextFragmentManager + `register_context_fragment` + 循环插槽） | 出现"每轮/高频刷新"的真实消费者（如 per-turn token 预算提醒） | codex world_state 实证版：注册式贡献通道（session/turn/event 三级 scope）、markers 幂等原位替换、预算裁决（2500 token 上限）、改写式钩子保留为逃生舱；机制归核心 `core/harness/context_fragments/`，内容归包；nova_agent 只加 `assemble_fragments` 插槽（transform_context 之前） |
| **随到随执行** | 用户重新拍板 | codex `turn.rs:2370` 对位：toolcall_end 起执行 future；信号与前端时序均验证过可行，本期搁置 |
| **审批策略化 + 沙箱升级重试** | executor 沙箱档真实启用后 | codex orchestrator 蓝本：审批→沙箱→拒绝升级重试（审批缓存不重复问）；permission_gate 硬编码换成声明式策略 |
| **模型主动申请**（切后端/提权限） | v1 稳定后 | codex `request_permissions` 对位 |
| **远程模式实测 + 二进制发布渠道** | ~~阶段一本地模式稳定后~~（远程模式已落地，见七） | 远程走 /ssh 模式（SSH 隧道，非 wss 公网）；**发布渠道仍挂账**——平台缓存件当前靠手工 seeding（`~/.nova/agent/executor/bin/<platform>/`），正式发布走 PyPI wheel 或内网静态目录 |
| **web 文件树面板**（远端工作区展示 + 点击下载） | web 宿主落地后 | executor fs 原语全有（readDirectory/walk/readStream） |
| **远程 rg 加速链**（ProcessRunner 缝） | ~~远程仓库检索体感慢（>10k 文件）~~（已落地，见七·检索加速） | 三级命运：远程 PATH rg（供给探测 `command -v rg` 白嫖）→ 平台缓存上传（可选增强，**不保证**）→ 便携引擎（永远成立的下限，有界并发保序）。缝实现：`executor/process_runner.py`——`ProcessRunner.spawn(argv, cwd)` 双实现（本地 asyncio subprocess / 远程 `process/start` 无壳 argv 直启），`rg --json` 行解析复用 `_collect_with_rg` 零改动，stderr 经 `output_with_stream` 流标签分离保真 |

## 判决记录（R 系列续）

- **R8：rg 永不内置进 nova-executor**。检索语义（pattern/glob/limit/hidden）
  烙进协议即边界倒退（`capabilityRoots` 死因同款）；ripgrep 无可嵌入库；
  缓存上传能解决的（分发/pin/覆盖）内置一个都多解决不了，还白付
  二进制膨胀与版本耦合。VS Code Remote 同为"独立二进制 + spawn"形态。
  重议条件：出现"跨主机检索行为必须字节级可复现"的硬契约（届时走
  缓存上传统一版本，仍不内置）。

## 七、阶段三：SSH 远程供给（/ssh 模式，已落地）

远程不监听公网端口、不做公网登陆——**身份层即 SSH**（类 VS Code
Remote-SSH）：远程 executor 只听 127.0.0.1，传输与身份全部走 SSH
通道，本地回环隧道转发。

### 用户路径

- `/executor remote user@host`（裸目标直输，ssh config 别名同享）或
  选择器末项"＋ 添加远程主机…"→ 输入框；
- **首次连接**：BatchMode 探测失败（auth）→ 终端让位执行原生 ssh
  （复用 `dialog:interactive-shell`），用户对着真 ssh 提示符**输一次
  密码**，同命令把 Nova 管理密钥（`~/.nova/agent/executor/id_ed25519`，
  首用自动生成）幂等装入远端 `authorized_keys`——之后永远 BatchMode
  免密。密码不经过 Nova 进程、不落盘；headless/无让位能力时不引导，
  直接报 `ssh-copy-id` 指引；connect 类失败（主机不可达）不引导；
- **供给成功后自动登记**进 settings `executor.endpoints`（缺省名 =
  host，经 `ctx.register_executor_endpoint` 写门）——下次选择器直接
  可选，无需手改 JSON；`/executor forget <name>` 移除；
- token 每次供给现生成（`secrets.token_hex`），经 ssh 命令行下发
  `--auth-token`，一次性、不落任何文件——SSH 是真身份层，token 只是
  隧道内防本机乱连的薄层；
- **远程执行 cwd**：远程文件系统与本地无关，本地 cwd 不能直接下发。
  用户可显式指定（`/executor remote user@host /data/proj`，`test -d`
  校验、随端点记忆）；缺省按**会话隔离工作区**——
  `<远程家目录>/.nova/agent/executor/workspaces/<session-id>`，切换时
  经刚建好的隧道 `mkdir -p` 落实。`BackendSelection`、会话条目
  （`remote_cwd`/`remote_shell`）、`ExecutorEndpoint.cwd`（仅显式目录
  进端点记忆）三处贯通；bash 引擎（LLM 工具 + user bash 同缝）执行期
  以 `remote_cwd` 替换本地 cwd；环境段 `<cwd>` 渲染执行 cwd、`<shell>`
  用探测到的远程登录 shell，`<workspace_roots>` 仍列本地（本地工具的
  事实）——模型由此区分两套路径语义。

### 供给管线（`nova_coding_agent/executor/provision.py`）

密钥就位 → BatchMode 探测（`uname -sm` + `pwd` + `$SHELL` + 远端二进制
存在性，一次 ssh 拿全）→ 缺则 scp 上传（本地缓存
`~/.nova/agent/executor/bin/<platform>/nova-executor` → 远端
`~/.nova/agent/executor/bin/nova-executor`，tmp+mv 原子替换）→
spawn：单 ssh 进程 `-tt -L lport:127.0.0.1:rport target 'exec
nova-executor --listen ws://127.0.0.1:rport --auth-token …'`。

**生命周期判决（R7）：PTY + exec 收 SIGHUP。** `exec` 使 executor 成为
远程会话 leader，连接一断（terminate / 断网 / 断电）sshd 即发 SIGHUP
回收——实证远程零孤儿。被否方案：**stdin 看门狗**（`cat` 守 channel
stdin，EOF 即 kill）——实证远程 executor 与任何读取 channel stdin 的
进程共存时启动即卡死（不打监听行、不绑端口），机制未查清但稳定复现，
弃用。PTY 副作用：onlcr（监听行 regex 兼容）、stderr 并入 stdout
（诊断归 `_wait_listen` 尾行缓冲）；`TERM=dumb` 前缀抑制交互式日志
变体。

### 连接管理（`executor/manager.py`）

- `get_client("ssh://…")` 分流到 SSH 供给（执行期**懒路径**：
  BatchMode-only——免密首连时就绪，之后永不需 UI）；
- `provision_ssh(target, on_progress, bootstrap)` 是 /executor 命令的
  eagerly 入口（footer 进度 + 首连引导回调）；
- **隧道死亡自动重供给**：缓存命中时校验本地 ssh 进程存活，死了就丢
  弃重供给（断网/休眠恢复场景）；
- **进程退出清理**：首个隧道/本地 spawn 创建时挂 `atexit` 钩子
  （`_sync_cleanup`）——后端进程正常退出（含 RPC 服务器信号优雅关停）
  即回收 ssh 隧道子进程与本地 executor；python 直接死亡不会回收子进程
  （实证孤儿会双重累积：本地 ssh + 远程 executor）。SIGKILL 无解——
  挂账：executor 侧空闲超时自毁；
- `ExecutorEndpoint.url` 直接承载 `ssh://[user@]host[:port]` 方案——
  settings schema 零改动；会话条目同样只存规范化 ssh:// URL，分支
  恢复只翻模式格，隧道首次执行时懒建立。

### fs 工具切换（read/write/edit/ls/find/grep——同一世界）

bash 之外，六个文件系统工具同样随后端切换（不然 bash 看远程盘、
read/write 看本地盘，两个世界）。架构：**一层低层
`FileSystemLayer`（`tools_common/fs_layer.py`——统一 fs 原语、全
async），六个 operations 实现参数化在它上面**——本地与远程是同一
实现类，远程切换只是注入不同的 layer，实现体零分叉：

- `LocalFileSystemLayer`：os/pathlib（to_thread）；`accelerates_search=True`；
- `ExecutorFileSystemLayer`（`executor/fs_layer.py`）：`ExecutorClient.fs`
  薄映射（`file://` URI 包装；`metadata` 不存在回 `exists=False` 而非
  抛错；`list_dir`/`walk` 前检保证与本地同语义异常；
  `accelerates_search=False`）；
- grep/find 加速链归 `executor/process_runner.py` 的 `ProcessRunner` 缝
  （`spawn(argv, cwd)` 无壳直启 + 行流/terminate/退出码/stderr 收集）：
  本地 asyncio 子进程 + `resolve_binary` 三级解析；远程 executor
  `process/start`（rg 路径随 SSH 供给探测 `command -v rg` 缓存，fd
  远程不解析）——`_collect_with_rg`/`_find_with_rg`/`_find_with_fd`
  共享 `_session_lines_with_abort` 读泵（abort 监听 terminate），
  便携引擎（walk + read + 正则/匹配，有界并发保序）在 runner 无
  rg/fd 时兜底；SDK 的 `fs/walk` 为本次补齐（Rust 服务端早已实现，
  缺省界限不超服务端上限）；**rg 永不内置进 executor（判决 R8）**；
- 工具执行期解析与 bash 同节奏：`_resolve_operations()` 读
  `backend_file_layer(context)`（远程 → ExecutorFileSystemLayer，
  本地/本地沙箱 → None 继续本地层）；路径解析统一
  `resolve_backend_path`（远程：相对→`remote_cwd`、~→`remote_home`、
  不查存在性不做 macOS 变体）；
- 环境段 `<workspace_roots>` 跟随后端（远程时 = 远程执行 cwd）——
  模型看到单一连贯世界；`question`/`todo`/`subagent` 不触碰执行环境，
  不在切换面（UI/会话态/进程内编排）；
- `permission_gate` 的写保护路径表是本机特定——远程路径天然不命中
  （v1 语义：远程不受该表约束，按后端分表是后续项）。

### 核心（nova_harness）新增

`SettingsManager.register_executor_endpoint / unregister_executor_endpoint`
（用户级写门，按 name upsert）+ ExtensionContext 同名两个 action
（actions/context/runner/agent 四处接线）。

### 验收记录

- 单测：provision 33 用例 + manager SSH 路由 6 用例 + 扩展 14 用例 +
  ExecutorBashOperations remote_cwd 覆盖 2 用例 + bash 工具缓存键 +
  fs 层 14 用例 + 远程切换 6 用例 + 便携 grep 并发流水线 4 用例 +
  ProcessRunner 11 用例（Local/Executor 双实现 + rg 调度/限流/stderr
  透传/便携回落）；
- e2e（真实服务器 180.184.33.245）：
  `frontend/scripts/ssh-executor-e2e.py`（供给全链路）、
  `ssh-executor-reconnect-e2e.py`（隧道死亡重供给）、
  `frontend/scripts/remote-fs-e2e.py`（六工具远程实操：write → read →
  edit → ls → grep → find 逐一断言）、
  `frontend/scripts/remote-rg-e2e.py`（探测远程 rg → grep/find 经远程
  rg --json/--files → glob 过滤 → 便携兜底结果一致）全过；
- **真 PTY 热切换 e2e**（`frontend/scripts/tui-hot-switch-e2e.py`，
  驱动真实 TUI）：/executor remote → 真实供给 + 会话工作区 →
  `!pwd`/`!hostname` 命中远程 → 模型答案原样引用
  `<environment_id>ssh://…</environment_id>` 与
  `<root>…/workspaces/…</root>`（系统提示词注入 + roots 跟随后端实证）→
  模型经 write/read 工具在远程工作区建文件并读回（服务器侧 `cat`
  实证内容精确）→ /executor local 热切回本地。
