# nova_protocol —— Nova 词汇枢纽

本包是 Nova 全部**跨组件边界词汇**的唯一住所。当前已入住：LLM 消息/内容/模型/用量/
流式事件、枚举与 compat、auth 词汇（含 `RedactedString` 脱敏类型）、模型目录存储条目
（`ModelsStoreEntry`）、agent 循环事件与 agent 消息词汇（`agent_events.py`：AgentEvent
家族 + AgentMessage 开放集 + CustomAgentMessage 基座 + AgentToolResult +
`dump_agent_message`）、诊断家族词汇（`enums.py` 的 `NovaErrorKind`/`WarningKind`、
`diagnostics.py` 的 `NovaDiagnostic`/`WarningEvent`）、exec-server 词汇域六模块
（`exec_server_config`/`environment`/`intent`/`policy`/`verdict`/`wire`——execpolicy
裁决引擎与安全白名单的 Python 移植住所）、出口清洗器（`sanitize.py` 的
`redact_secrets`）、语义 id、共享别名（`aliases.py`）、取消原语（`signal.py`）。
模型目录另有 `Model`/`ImageModel`/`ClassifierModel` 的 `AnyModel` 判别联合与 `ModelType`；独立图像与分类结果词汇分别为 `ImagesContext`/`AssistantImages`/`ImagesStopReason` 与 `classifier.py` 的 `ClassifierContext`/`ClassifierResult`/`ClassifierStopReason`。
按迁移路线待迁入：session 落盘 entries、线上 item 正典、共享 config 形状。

## 收录标准（唯一一条）

> 这个类型是否**跨组件边界被序列化**（或跨包共享的纯形状）？是 → 进本包；否 → 回其语义所有者。

## 三纪律

1. **依赖最小**：运行时依赖只允许 `pydantic`（见 `tests/test_purity.py` 机械执法）；
2. **零 I/O、行为登记制**：本包主体只有形状 + 平凡不变量（validator / computed /
   `to_*`·`from_*` 纯转换）。类型需要行为时以消费方扩展函数安置，行为函数写在消费方
   模块，不进本包。缓冲带是**登记制**的两类成员（纯洁性审计白名单即登记册）：
   ① **基础原语**——全仓签名通用、零业务语义的极小机制，当前成员 `signal.py`
   （取消原语词汇，允许 import asyncio；取消/等待**机制件**——竞速等待、真杀
   scope、任务台账——已升格迁出归 `nova_async` 叶子包）；② **纯函数算法件**——
   零 I/O、仅 stdlib、输入输出都是本包词汇的随行算法，当前成员
   `exec_server_intent.py`（shell 解析/危险命令判定）、`exec_server_policy/`
   （策略规则求值）、`exec_server_permissions/`（权限套餐继承解析、编译、选择、
   路径与网络配置纯算法）、`sanitize.py`（`redact_secrets`）。登记成员持续增多时升格
   为独立叶子包，枢纽恢复绝对纯形状；
3. **单一正典源**：每个类型在本包有且只有一个定义点。消费方一律
   `from nova_protocol import X`（`__init__.py` 全量门面再导出，内部文件布局自由）。

## 双序列化出口（出厂配置）

`NovaBaseModel` 在此定义，全部枢纽类型继承：

- `model_dump()`——持久化/内部形态（snake_case 字段名，磁盘存量兼容）；
- `dump_wire()`——线上形态（`by_alias=True` camelCase）。

边界（JSONL 读入 / RPC 收参 / 包加载）一律 `model_validate()` 全量校验；
内部自产自销一律 `model_construct()` 跳过校验。
