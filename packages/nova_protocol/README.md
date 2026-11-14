# nova_protocol —— Nova 共享领域数据词汇

> 状态：【现状】共享纯数据、取消机制、Auth 行为、出口清洗与 exec 边界已按 `docs/crosscutting/data-modeling.md` §一完成分址，纯洁性审计常绿。

本包是 Nova **跨领域共享数据词汇**的唯一定义点，不是全部共享代码的收容所。终态住户包括：

- `base_model.py` 的 `NovaBaseModel`；
- `ids.py`、`aliases.py`、`diagnostics.py` 的跨域基础设施与诊断词汇；
- LLM 消息、内容、模型、用量、流式事件、枚举与 compat；
- 模型目录字段包与 `ModelsStoreEntry`；
- 图像与分类词汇；
- `identity.py` 的客户端身份与归因词汇；
- `agent_events.py` 的 Agent 循环事件、Agent 消息开放集与 `AgentToolResult`；
- Auth 公共数据：`Credential`、`RedactedString`、`AuthResult`、`AuthCheck`、Auth 交互数据等。

## 收录标准

一个类型必须同时满足以下条件才进本包：

1. 有真实的跨领域消费方；
2. 是纯数据或值对象，只含字段、枚举、validator、计算属性、平凡序列化转换；
3. 需要统一的正典表示（落盘、上线，或跨组件共享同一语义）；
4. 不是某个外部协议的特定版本面；
5. 不是单一行为实现的私有入参、运行时容器或中间态。

**跨包使用、会序列化、纯函数，单独任一条件都不足以入住。**

## 三纪律

1. **依赖最小**：运行时依赖只允许 `pydantic`；
2. **零 I/O、零行为例外**：只放形状 + 平凡不变量。Callable 配置、服务 Protocol、异步原语、出口清洗、Shell/策略/权限算法均不得入住；不设行为登记制或白名单；
3. **单一正典源**：每个类型只有一个定义点，公共消费方从本包门面 `
from nova_protocol import X` 导入；边界包不得把本包类型中转成自己的正典。

## 不住本包的内容

- `nova_async`：`AbortSignal` / `AbortController` / `AbortedError` 与取消等待机制；
- `nova_ai/auth`：Auth 行为定义、服务 Protocol、Callable 配置与流程私有异常；
- `nova_ai/utils/sanitize.py`：`redact_secrets` 与 `REDACTED_SECRET`；
- `nova_exec_protocol`：executor 版本化线上 wire；
- `nova_exec_policy`：Shell 分析、策略求值、权限编译、配置物化与策略词汇；
- `nova-exec-server-client`：连接期环境类型；
- `nova_harness`：Session 持久化与审批 UI 词汇；
- `nova_server`：前端 RPC、线上 item 与 notification。

## 双序列化出口（出厂配置）

`NovaBaseModel` 在此定义，本包中的 Pydantic 类型继承：

- `model_dump()`——持久化/内部形态（snake_case 字段名，磁盘存量兼容）；
- `dump_wire()`——线上形态（`by_alias=True` camelCase）。

边界（JSONL 读入 / RPC 收参 / 包加载）一律 `model_validate()` 全量校验；内部自产自销一律 `model_construct()` 跳过校验。
