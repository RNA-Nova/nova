# Nova vs pi：扩展事件词汇与结果契约对比（第三张表）

> 扩展系统的三张学习表：注册面（`nova`/`pi`）、运行面（`ctx`）、**事件与结果契约**。
> 本文对比第三张——扩展能在哪些时刻介入（事件词汇），以及介入能改变什么
> （handler 返回值契约）。挂载点对比见 `nova_vs_pi_api_ctx.md`。
>
> 结论先行：**本表两家 ~90% 同构**（刻意对齐），差异全在边缘，无结构性分歧。

## 一、分派语义（两家一致）

| 语义 | 规则 | 适用事件 |
|---|---|---|
| 观察式 | handler 只读、异常隔离（不影响主流程） | agent/turn/message/tool_execution 等通知类 |
| 拦截-短路 | 首个 `block`/`handled` 立即返回，不再执行后续 handler | `tool_call`、`input` |
| 拦截-链式 | 后续 handler 在前者改写结果上继续改写 | `tool_result`、`message_end`、`input`(transform)、`before_provider_request` |
| 最后非 None | 收集最后一个有效结果；`cancel` 短路 | `session_before_*` |
| 安全姿态 | `tool_call` handler 异常 → **fail-closed**（视为 block，不放行）；其余事件 fail-open（转 error 事件） | 两家对齐 |

## 二、事件词汇对照

| 事件 | pi | nova | 说明 |
|---|---|---|---|
| **—— 会话生命周期 ——** | | | |
| `session_start` / `session_shutdown` | ✅ | ✅ | |
| `session_before_switch` | ✅ | ✅ | 可 cancel |
| `session_before_fork` | ✅ | ✅ | 可 cancel + skipConversationRestore |
| `session_before_compact` / `session_compact` | ✅ | ✅ | 前者可 cancel 并覆盖压缩参数 |
| `session_before_tree` / `session_tree` | ✅ | ✅ | 树导航（带摘要定制） |
| `session_info_changed` | ✅ | ✅ | nova 已双发对齐（`set_session_name` 同步方法经 create_task fire-and-forget 到 runner，pi 同款 `void emit`） |
| **—— Agent loop（桥接） ——** | | | |
| `agent_start` / `agent_end` | ✅ | ✅ | nova 经 `forward_to_runner` 桥接映射 |
| `agent_settled`（agent_end 且队列排空） | ✅ | ✅ | nova 已对齐：`_run_agent_prompt` 的 finally 双发（正常/abort/异常均发射），进 wire schema |
| `turn_start` / `turn_end` | ✅ | ✅ | |
| `message_start` / `message_update` | ✅ | ✅ | 流式 |
| `message_end` | ✅ | ✅ | **可改写**（两家都做原地替换） |
| `tool_execution_start` / `update` / `end` | ✅ | ✅ | 观察式 |
| `model_select` / `thinking_level_select` | ✅ | ✅ | 通知 |
| **—— 拦截钩子 ——** | | | |
| `tool_call` | ✅ | ✅ | block 短路 + **fail-closed**（两家对齐；详见下节契约） |
| `tool_result` | ✅ | ✅ | 链式改写 content/details/is_error |
| `input` | ✅ | ✅ | continue / transform / handled |
| `context` | ✅ | ✅ | 改写 LLM 消息列表 |
| `before_agent_start` | ✅ | ✅ | 注入消息 / 替换系统提示词 |
| `before_provider_request` | ✅ | ✅ | 改写请求 payload |
| `before_provider_headers` | ✅ | ✅ | nova 已对齐：handler 原地改 headers（返回值忽略）、fail-open，经 `transform_headers` 挂进 provider 请求链 |
| `after_provider_response` | ✅ | ✅ | 观察式 |
| `user_bash` | ✅ | ✅ | 可整体替换执行结果 / 注入自定义 operations |
| `input` 之外的用户侧 | — | — | |
| **—— 资源与信任 ——** | | | |
| `project_trust` | ✅ | ✅ | 裁决 yes/no/undecided + remember |
| `resources_discover` | ✅ | ✅ | 贡献额外 skill/prompt/agent 路径 |
| **—— loop 控制（nova 独有） ——** | | | |
| **`prepare_next_turn`** | ❌ | ✅ | 下一轮准备（可注入/调整） |
| **`should_stop_after_turn`** | ❌ | ✅ | 扩展可决定本轮后终止 loop——pi 的 loop 不对扩展开放这一层 |
| **—— 诊断 ——** | | | |
| 扩展错误扇出 | ✅（`errorListeners`） | ✅（`on_error` / `extension_error`） | 两家同有，措辞不同 |

## 三、结果契约对照（可拦截事件的返回值）

| 事件 | pi | nova | 对齐度 |
|---|---|---|---|
| `tool_call` | `{block, reason}` + 文档化的 input 原地改参（mutate 后不再二次校验） | `{block, reason}` + 原地改 `args`（**已文档化 + 测试钉死**：事件与执行参数共享同一 dict，改后不再二次校验） | ✅ |
| `tool_result` | `{content, details, isError}` 链式 | `{content, details, is_error}` 链式 | ✅ |
| `input` | `{action: continue/transform/handled, text, images}` | 同 | ✅ |
| `context` | `{messages}` | 同 | ✅ |
| `before_agent_start` | `{message, systemPrompt}`（多扩展链式） | `{message, system_prompt}` | ✅ |
| `message_end` | `{message}`（保 role 原地替换） | 同（`replace_message_in_place`，role 校验） | ✅ |
| `before_provider_request` | `unknown`（payload 改写） | `{payload}` | ✅ |
| `user_bash` | `{operations, result}` | 同 | ✅ |
| `session_before_switch` | `{cancel}` | 同 | ✅ |
| `session_before_fork` | `{cancel, skipConversationRestore}` | `{cancel, skip_conversation_restore}` | ✅ |
| `session_before_compact` | `{cancel, compaction}` | 同 | ✅ |
| `session_before_tree` | `{cancel, summary, customInstructions, replaceInstructions, label}` | 同 | ✅ |
| `project_trust` | `{trusted: yes/no/undecided, remember}` | 同 | ✅ |
| `resources_discover` | `{skillPaths, promptPaths, …}` | 同 | ✅ |
| `should_stop_after_turn` | — | `{stop}` | nova 独有 |

## 四、差异清单

**事件词汇：已完全对齐，pi 无剩余独有事件。**

（本轮补齐三项：`before_provider_headers`——handler 原地改 headers、fail-open，经 `transform_headers` 挂进请求链，8 条测试；`session_info_changed`——`set_session_name` 双发 runner；`agent_settled`——`_run_agent_prompt` 的 finally 双发、正常/abort/异常均发射、进 wire schema，4 条测试。）

**nova 多 2 个事件**（反超项）：

- `prepare_next_turn` / `should_stop_after_turn`——loop 控制钩子，pi 的 loop 不对扩展开放这一层。

**契约差已清零**：`tool_call` 的原地改参已文档化（`ToolCallEvent` docstring：args 与执行参数共享同一 dict、串行 handler 链式可见、改后不再二次校验）并经双层测试钉死（nova_agent 环级 + harness 扩展级引用透传）。

**分派与安全语义**：无差异（短路/链式/最后非 None/异常隔离/tool_call fail-closed，全对齐）。
