<!-- AGENTS.md - Nova Monorepo 项目指南（索引与随身规则） -->

# Nova —— LLM Agent 构建框架（Monorepo）

> 本文件是开机必读：项目一句话、文档地图、仓库地图、命令速查与随身规则。
> 深入内容已分到 `docs/` 专题文档——按指针阅读，不要假设本文件包含全部细节。

## 项目概览

Nova 是一个用于构建大语言模型（LLM）智能体的 **Python 单体仓库（monorepo）**。项目采用分层架构，将 LLM 提供商抽象、Agent 核心框架、高阶 SDK、专用 Agent bundle 与前端拆分为独立的子包，便于按需组合与独立迭代。

- **目标语言**：Python `>=3.13,<3.14`；`nova-client` 前端额外需要 Node.js `>=22.19.0`
- **项目语言**：代码注释与文档主要使用**中文**
- **当前阶段**：Alpha（`0.1.x` 发布线在备份仓 `legacy/0.1.x` 分支；本仓 main 为新架构线）
- **License**：MIT　**作者**：Liujinming

## 文档地图

| 文档 | 内容 | 何时读 |
|---|---|---|
| `docs/README.md` | 建模体系总图（纵线/横切/边界坐标系 + 分工矩阵 + 状态约定） | 任何标准类改动先在此找家 |
| `docs/architecture.md` | Monorepo 结构、各子包详细组织、路径约定 | 动包结构/跨包改动前 |
| `docs/crosscutting/data-modeling.md` | 数据建模标准全文（住所/选型/序列化/演化/测试） | 新增或迁移任何类型前 |
| `docs/boundary/protocol-modeling.md` | 协议层建模标准（分层/正典源/握手/演化/线上错误/传输/生成物） | 新增 RPC 方法/事件/item、动线上契约前 |
| `docs/crosscutting/diagnostics-modeling.md` | 诊断与可观测性建模标准（错误/警告/通知/诊断/日志/遥测：分类学/通道/持久化） | 新增任何诊断类词汇、异常类型、告警/通知通道前 |
| `docs/crosscutting/localization.md` | 语言与本地化建模标准（注释/文档/commit/产品文案/诊断原文/模型文本的语言与住所） | 动用户面文案、加错误消息文本、做 i18n 前 |
| `docs/crosscutting/testing-modeling.md` | 测试建模标准全文（住所/分层/假缝/断言/工具栈） | 新增或重构任何测试前 |
| `docs/development.md` | 环境、构建、测试、格式化、发布命令 | 搭环境/跑测试/发布前 |
| `docs/crosscutting/security.md` | API Key、会话数据、文件安全、Project Trust | 碰鉴权/文件操作前 |
| `packages/nova_ai/AGENTS.md` | nova_ai 包级指南 | 深入改 nova_ai 前 |
| `packages/nova_harness/AGENTS.md` | nova_harness 包级指南（最全） | 深入改 nova_harness 前 |

## 仓库地图

| 包 | 角色 |
|---|---|
| `packages/nova_protocol` | 跨领域共享纯数据词汇（LLM/Agent/Auth/ID/诊断），零行为、零兄弟包依赖 |
| `packages/nova_ai` | LLM provider 抽象层（聊天流式、非流式图像生成与结构化分类、鉴权、混合模型注册）+ 包内 `catalog/` 模型目录构建管线（对位 pi `packages/ai/catalog/`） |
| `packages/nova_async` | 完整取消词汇与等待机制族叶子包（AbortSignal/AbortScope/竞速/退避/TaskTracker/KeyedLock/Deadline） |
| `packages/nova_exec_protocol` | executor 独立版本化 wire DTO |
| `packages/nova_exec_policy` | Shell 分析、执行策略、权限编译与配置物化 |
| `packages/nova_agent` | 事件驱动异步 Agent 循环核心 |
| `packages/nova_harness` | 高阶 SDK：会话树/压缩/资源/包管理/RuntimeManager |
| `packages/nova_server` | JSON-RPC 协议服务层 + client 家族 + headless exec |
| `packages/nova_client` | TS 前端运行时（TUI 宿主在内） |
| `packages/nova-exec-server` | Rust 通用执行后端（进程/fs/PTY/沙箱） |
| `packages/nova-exec-server-client` | executor 的 Python 薄客户端 |
| `bundles/nova_coding_agent` | 官方编程 bundle（10 工具 + 6 扩展 + agents） |

数据建模现状：`nova_protocol` 共享纯数据词汇＋边界协议包＋行为机制与组件内部类型，已完成分址——见 `docs/crosscutting/data-modeling.md`「住所」。

## 命令速查

```bash
pixi install -e dev        # 环境初始化（dev 含 black/isort/pytest）
pixi run -e dev test-all   # 十个测试套件全量（各包独立跑，避免 conftest 冲突）
pixi run -e dev format     # black + isort 全仓
pixi run -e dev typecheck  # pyright（protocol/async/ai/agent/harness/server + exec 协议与策略）
cd packages/nova_client && npm run build   # TS 前端编译
```

## 代码风格指南

- **类名**：`PascalCase`；**函数 / 变量**：`snake_case`；**常量**：`UPPER_CASE`
- **导入排序**：`isort`（`profile = "black"`、`multi_line_output = 3`、`include_trailing_comma = true`）；**格式化**：`black`（`py313`）
- **注释与文档字符串**：以**中文**为主，保持与现有代码一致

## 数据建模速览

> 全文：`docs/crosscutting/data-modeling.md`。以下速览覆盖日常决策；拿不准时读全文。

- **住所总规则**：跨领域且需统一正典的纯数据进 `nova_protocol`；其余按语义所有权归位。四问判定：单一领域或行为私有入参→业务包；Callable/Protocol/状态/IO/算法→机制包；外部版本契约→边界协议包；跨领域纯数据→枢纽。包内以领域内聚优先，单一公共类型领域可用 `types/` 或 `types.py` 表达，跨领域混合类型收容层不立；外部版本协议只入 `protocol/`。**单一正典源**：枢纽类型从 `nova_protocol` 进，业务类型从语义所有方公共入口进，不经二手中转。
- **选型决策序**：可变→普通 class/dataclass；低频契约→Pydantic（`NovaBaseModel`）；Callable/服务/异常→dataclass；不可变值对象→`frozen=True`；union 必判别；哑容器不进 Pydantic。
- **序列化**：`model_dump()`（持久化 snake）与 `dump_wire()`（线上 camel）双出口不混用；边界 `model_validate`、内部 `model_construct`。
- **演化**：加 optional/加联合成员 = minor；删改字段/改判别键 = major。线上与落盘（JSONL v3）双契约共用。
- **开放集**：第三方词汇走注册通道（`NovaItem` 子类 + `CustomItem` 兜底 + 命名空间式 type 名），不进枢纽。

## 工作规则（雷区与惯例）

- **修改前先确认所属子包**：各子包有独立 `pyproject.toml` 与依赖，不要混用。
- **不要假设测试一定通过**：以当前实跑为准；改关键逻辑后在对应子包跑测试确认（命令见速查）。
- **序列化层**：新增数据类先按数据建模标准的决策顺序选型；Pydantic 一律继承 `NovaBaseModel`。
- **新增 Agent / 工具 / 扩展**：参考 `bundles/nova_coding_agent` 的 `[tool.nova]` 段与目录结构；`nova-pkg init` 可自动生成该段。
- **子包级 AGENTS.md**：深入改 `nova_ai` / `nova_harness` 前先读对应包级指南（见文档地图）。

## 版本与变更

- 当前版本：`0.1.0`（Alpha，main 新架构线）；`nova-coding-agent` bundle 版本为 `1.0.0`
- 变更日志：根目录 `CHANGELOG.md` 记录仓库级变更；子包中 `nova_ai`/`nova_agent` 各有 0.1.0 封版条目，其余子包尚未建立。
