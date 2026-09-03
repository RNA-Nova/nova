# Session / Compaction / Messages 与 TS (pi coding-agent) 对齐核对表

> 清单式核对：`pi/packages/coding-agent/src/core`（session-manager.ts、compaction/、messages.ts）
> 的每一个公开导出与 `nova_harness/core/harness/{session,compaction}`、`core/types/{session,messages}`、
> `core/utils/messages` 的逐项对应关系。
>
> 状态图例：
> - ✅ 对齐（行为等价）
> - ⚠️ 有意差异（已评估并拍板保留）
> - ➖ 不适用（TS 有但 Python 第一版不需要 / Python 独有）
>
> 更新时间：2026-07-20（第六轮复核的 7 项新发现已全部修复并验证）

---

## A. session-manager.ts 模块级导出

| TS 导出 | Python 对应物 | 状态 | 备注 |
|---|---|---|---|
| `CURRENT_SESSION_VERSION = 3` | `types/session/constants.py: CURRENT_SESSION_VERSION = 3` | ✅ | 第一版直接写 v3 |
| `SessionHeader` | `types/session/entries.py: SessionHeader` | ✅ | snake_case 字段名 |
| `SessionEntry` union（9 种条目） | `types/session/entries.py: SessionEntry`（同 9 种） | ✅ | LeafEntry/ActiveToolsChangeEntry 已按现行层移除 |
| `FileEntry` | `types/session/entries.py: FileEntry` | ✅ | |
| `SessionTreeNode` | `types/session/tree.py: SessionTreeNode`（含 label_timestamp） | ✅ | |
| `SessionContext` | `types/session/context.py: SessionContext` | ⚠️ | `model` 为 `Tuple[str,str]`（TS 为 `{provider,modelId}` 对象）；无生产消费方 |
| `SessionInfo` | `types/session/info.py: SessionInfo` | ✅ | snake_case（死代码 `model_validator` 已删） |
| `NewSessionOptions{id,parentSession}` | `create/in_memory/new_session` 的 `session_id`/`parent_session` 关键字参数 | ✅ | 风格差异：options 对象 → 关键字参数 |
| `ReadonlySessionManager`（Pick） | `types/protocols.py: SessionManagerProtocol` | ⚠️ | 方法集按 Python 消费面裁剪，非机械对应 |
| `assertValidSessionId` | `utils.py: assert_valid_session_id` | ✅ | 同一正则 |
| `generateId`（内部） | `utils.py: generate_id`（公开导出） | ⚠️ | 碰撞回退不同：Python 16 hex vs TS 完整 UUID；无行为影响 |
| `migrateV1ToV2 / migrateV2ToV3 / migrateSessionEntries` | 无 | ➖ | Python 第一版直接 v3，无历史格式 |
| `parseSessionEntries` | `utils.py: parse_session_entries`（pydantic 判别式） | ⚠️ | TS 无校验 parse；Python 校验式，畸形行跳过 |
| `parseSessionEntryLine`（内部） | `utils.py: parse_session_entry_line`（公开） | ✅ | Python 公开（models.py 复用） |
| `getLatestCompactionEntry` | `utils.py: get_latest_compaction_entry` | ✅ | |
| `sessionEntryToContextMessages` | `utils.py: session_entry_to_context_messages` | ✅ | TS 在投影关口修复 `content==null`（保留 entry、补空 content）；Python 在加载关口等价修复（`parse_session_entry_line` 失败重试路径 `_repair_null_message_content`，正常行零开销），entry 同样保留 |
| `buildContextEntries` | `utils.py: build_context_entries` | ✅ | |
| `buildSessionContext` | `utils.py: build_session_context` | ⚠️ | `leafId` 三分语义用 `_USE_LAST_ENTRY` sentinel（Python 无 undefined/null 区分） |
| `getSessionContextSettings`（内部） | `utils.py: _get_session_context_settings`（二元组） | ✅ | Python thinking_level 用 None 表示 off（TS 用 `"off"`） |
| `getDefaultSessionDirPath / getDefaultSessionDir` | `utils.py: get_default_session_dir_path / get_default_session_dir` | ✅ | 含 `:` 替换（Windows） |
| `getDefaultSessionDir(cwd, agentDir?)` 的 agentDir 参数 | 无参数；`config/defaults.get_agent_dir()` 支持 `NOVA_AGENT_DIR` 环境变量 | ⚠️ | 功能等价，机制不同（参数 vs 环境变量） |
| `loadEntriesFromFile` | `utils.py: load_entries_from_file`（流式逐行） | ✅ | |
| `readSessionHeader / sessionCwdMatches`（内部） | `utils.py: _read_session_header` / `find_most_recent_session` 内联 | ✅ | Python 用 readline 读整行，无 TS 的 512 字节截断（更健壮） |
| `findMostRecentSession(sessionDir, cwd?)` | `utils.py: find_most_recent_session(session_dir, cwd=None)` | ✅ | cwd 过滤已对齐 |
| `extractTextContent` | `core/utils/messages.py: extract_text_from_content`（content 级） | ✅ | `" ".join` 一致；参数层级不同（content vs message） |
| `getMessageActivityTime`（内部） | `utils.py: message_activity_time`（毫秒统一） | ✅ | 曾有时间单位混合 bug，已修 |
| `buildSessionInfo`（内部） | `models.py: _build_session_info_sync`（单次流式扫描） | ✅ | `created` 非法时 fallback mtime（对齐 TS 不丢会话） |
| `buildSessionInfosWithConcurrency`（内部） | `models.py: asyncio.Semaphore(10)` + gather | ✅ | 生态适配（线程池） |
| `listSessionsFromDir`（内部） | `models.py: list_sessions_from_dir`（含 progress offset/total） | ✅ | |
| `SessionListProgress` 类型 | `Callable[[int,int], None]` 注解 | ✅ | |
| —（Python 独有） | `utils.py: is_valid_session_file` | ➖ | TS 的 readSessionHeader 不导出 |
| —（Python 独有） | `utils.py: get_last_activity_time` | ➖ | TS 内联在 buildSessionInfo 里 |

## B. SessionManager 静态方法

| TS | Python | 状态 | 备注 |
|---|---|---|---|
| `create(cwd, sessionDir?, options?)` | `create(cwd, session_dir=None, session_id=None, parent_session=None)` | ✅ | |
| `open(path, sessionDir?, cwdOverride?)` | `open(path, session_dir=None, cwd_override=None)` | ✅ | |
| `continueRecent(cwd, sessionDir?)` | `continue_recent(cwd, session_dir=None)` | ✅ | filterCwd 逻辑已对齐 |
| `inMemory(cwd?, options?)` | `in_memory(cwd="", session_id=None, parent_session=None)` | ✅ | |
| `forkFrom(sourcePath, targetCwd, sessionDir?, options?)` | `fork_from(source_path, target_cwd, session_dir=None, session_id=None)` | ✅ | 独占创建 `"x"`（对齐 `"wx"`）；复制走**行级原文**——合法 JSON 行（含未知类型条目）原样保留，非法行丢弃，磁盘层 fork 零丢失 |
| `list(cwd, sessionDir?, onProgress?)` | `list_sessions(cwd, session_dir=None, on_progress=None)` | ✅ | filterCwd 已对齐 |
| `listAll(onProgress?) / listAll(sessionDir?, onProgress?)` | `list_all_sessions(session_dir=None, on_progress=None)` | ✅ | 自定义目录参数已补 |

## C. SessionManager 实例方法

| TS | Python | 状态 | 备注 |
|---|---|---|---|
| `setSessionFile(sessionFile)` | `set_session_file(session_file)` | ✅ | 非空损坏文件抛错（对齐）、空文件初始化 |
| `newSession(options?)` | `new_session(session_id=None, parent_session=None)` | ✅ | |
| `isPersisted()` | `is_persisted()` | ✅ | |
| `getCwd() / getSessionDir() / getSessionId() / getSessionFile()` | 同名 snake_case | ✅ | |
| `usesDefaultSessionDir()` | `uses_default_session_dir()` | ✅ | session_dir 已规范化 |
| `_persist(entry)` | `_persist_entry(entry)` | ✅ | flushed 延迟创建 + `"x"` 独占首写 |
| `JSON.stringify(entry)`（落盘序列化） | `entry_to_json(entry)`（模块级函数） | ✅ | 紧凑分隔符（`separators=(",", ":")`）+ `exclude_none=True`（None 键省略，对齐 TS undefined 键不落盘），逐字节形状一致 |
| `appendMessage(message)` | `append_message(message)` | ✅ | Python 附赠 pydantic 运行时校验（比 TS 严格，红利） |
| `appendThinkingLevelChange(level)` | `append_thinking_level_change(level=None)` | ⚠️ | None 表示 off |
| `appendModelChange(provider, modelId)` | `append_model_change(provider, model_id)` | ✅ | |
| `appendCompaction(...) → string` | `append_compaction(...) → CompactionEntry` | ⚠️ | Python 返回 entry 对象，调用方（controllers/compaction.py）消费归一化后的 `entry.details`，有实际消费者；details 过 `_normalize_details` |
| `appendCustomEntry(customType, data?)` | `append_custom_entry(custom_type, data=None)` | ✅ | data 同样过归一化 |
| `appendSessionInfo(name)` | `append_session_info(name)` | ✅ | `\r\n → 空格` sanitize 一致 |
| `getSessionName()` | `get_session_name()` | ✅ | 最新一条决定，空名=显式清除 |
| `appendCustomMessageEntry(...)` | `append_custom_message_entry(...)` | ✅ | details 过归一化 |
| `getLeafId() / getLeafEntry() / getEntry(id)` | 同名 snake_case | ✅ | |
| `getChildren(parentId)` | `get_children(parent_id)` | ✅ | |
| `getLabel(id)` | `get_label(entry_id)` | ✅ | |
| `appendLabelChange(targetId, label)` | `append_label_change(target_id, label)` | ✅ | 含 label_timestamps 索引 |
| `getBranch(fromId?)` | `get_branch(from_id=None)` | ✅ | |
| `buildContextEntries()` | `build_context_entries()` | ✅ | |
| `buildSessionContext()` | `build_session_context()` | ✅ | reset_leaf 后为空（对齐） |
| `getHeader()` | `get_header()` | ✅ | |
| `getEntries()` | `get_entries()` | ✅ | |
| `getTree()` | `get_tree()` | ✅ | 迭代排序（防深树溢出） |
| `branch(branchFromId)` | `branch(branch_from_id)` | ✅ | 纯内存（LeafEntry 已移除） |
| `resetLeaf()` | `reset_leaf()` | ✅ | 纯内存 |
| `branchWithSummary(...)` | `branch_with_summary(...)` | ✅ | 纯内存 leaf + details 归一化 |
| `createBranchedSession(leafId)` | `create_branched_session(leaf_id)` | ✅ | parent 重链 + label 原时间戳 + 单 timestamp |

## D. compaction/（compaction.ts）

| TS | Python | 状态 | 备注 |
|---|---|---|---|
| `CompactionSettings / DEFAULT_COMPACTION_SETTINGS` | `types/compaction: CompactionSettings / DEFAULT_COMPACTION_SETTINGS` | ✅ | 16384/20000 一致 |
| `CompactionResult / CompactionPreparation / CutPointResult / ContextUsageEstimate / CompactionDetails` | `types/compaction/` 同名类型 | ✅ | details 键名 snake_case（格式全局约定） |
| `calculateContextTokens / getLastAssistantUsage / estimateContextTokens / shouldCompact` | 同名函数 | ✅ | 含零 usage 校验 |
| `estimateTokens` | `estimate_tokens` + `estimate_messages_tokens` | ✅ | chars/4；image 4800（`ESTIMATED_IMAGE_CHARS`）；JSON 紧凑序列化。`estimate_messages_tokens` 在 TS 是 agent-session.ts 私有函数，Python 导出（更可用） |
| `findCutPoint / findTurnStartIndex` | `find_cut_point / find_turn_start_index` | ✅ | cut/turn-start 规则一致 |
| `generateSummary` | `generate_summary` | ✅ | 参数顺序/0.8 系数/update prompt 一致 |
| `prepareCompaction` | `prepare_compaction` | ✅ | 边界/空摘要短路一致（短路位置同样在 fileOps 提取之前） |
| `compact` | `compact` | ✅ | split-turn **串行**（对齐）、`<read-files>` 拼接一致 |
| `createSummarizationOptions` | `_create_summarization_options` | ✅ | |
| `completeSummarization` | `complete_summarization`（公开） | ⚠️ | 含 `isawaitable` 分支（Python 双形态 stream_fn 生态适配）；回退 `builtin_models().complete_simple` 每次新建 Models（无模块级状态原则） |
| `_SUMMARIZATION_PROMPT / _UPDATE_SUMMARIZATION_PROMPT / _TURN_PREFIX_SUMMARIZATION_PROMPT` | 同名常量 | ✅ | 逐字一致（`[x]`、"an AI assistant"） |
| `env / headers / stream_fn 参数` | 同名参数 | ✅ | 编排层 `get_summarization_request_auth` 统一供给 |

## E. compaction/（branch-summarization.ts + utils.ts）

| TS | Python | 状态 | 备注 |
|---|---|---|---|
| `collectEntriesForBranchSummary` | `collect_entries_for_branch_summary` | ✅ | |
| `prepareBranchEntries` | `prepare_branch_entries` | ✅ | 两趟扫描 + 90% 规则一致 |
| `generateBranchSummary` | `generate_branch_summary` | ✅ | aborted/error 语义、2048 max_tokens、不带 thinking 一致 |
| `GenerateBranchSummaryOptions` | `types/compaction/branch_summary.py` | ✅ | `signal` 必填（已对齐 TS 契约）；含 env/headers/stream_fn |
| `BranchSummaryResult / BranchPreparation / CollectEntriesResult` | 同名类型 | ✅ | |
| `createFileOps / extractFileOpsFromMessage / computeFileLists / formatFileOperations` | 同名函数 | ✅ | read/write/edit 三集合语义一致 |
| `serializeConversation` | `serialize_conversation` | ✅ | 2000 字符截断一致 |
| `SUMMARIZATION_SYSTEM_PROMPT` | 同名常量 | ✅ | 逐字一致 |
| `getMessageFromEntryForCompaction`（内部） | `get_message_from_entry`（委托 `session_entry_to_context_messages`） | ✅ | Python 统一公共函数（带 skip 参数），消除 TS 的两份私有重复 |

## F. messages.ts

| TS | Python | 状态 | 备注 |
|---|---|---|---|
| `COMPACTION_SUMMARY_PREFIX/SUFFIX`、`BRANCH_SUMMARY_PREFIX/SUFFIX` | `types/messages.py` 同名常量 | ✅ | 逐字一致 |
| `BashExecutionMessage / CustomMessage / BranchSummaryMessage / CompactionSummaryMessage` | `types/messages.py` 同名类型 | ⚠️ | timestamp 必填 int（已对齐）；无 FileContent（已删，将来仅作 RPC 传输载体） |
| `bashExecutionToText` | `BashExecutionMessage.to_context_text()` | ✅ | 已多态化：静态分派改为消息方法（user tool 设计，见 `user_tools_design.md`） |
| `createBranchSummaryMessage / createCompactionSummaryMessage / createCustomMessage` | 同名工厂 | ✅ | `_parse_timestamp` 非法字符串回退 0（对齐 TS `new Date(s).getTime()` 得 NaN 的静默语义；Python int 无 NaN 取 0） |
| `convertToLlm` | `convert_to_llm`（同步） | ✅ | 各分支一致；返回注解已放宽为 `List[Message]`（user/assistant/toolResult 原样透传） |
| `CustomAgentMessages` declaration merging | `nova_agent.CustomAgentMessage` 基类 | ⚠️ | 扩展机制不同（TS 声明合并 vs Python 基类），效果等价 |

---

## 汇总

- **✅ 对齐**：绝大多数条目——算法、事件契约、持久化语义、容错行为逐项一致。
- **⚠️ 有意差异（已拍板）**：
  1. 会话 JSONL 全量 snake_case；
  2. `thinking_level` 用 None 表示 off（TS `"off"`）；
  3. `append_compaction` 返回 `CompactionEntry` 对象（TS 返回 id）；
  4. pydantic 校验式加载（未知类型/畸形行跳过 vs TS 宽容保留；null-content 修复策略已对齐，见 A 区）；
  5. `_normalize_details` 写入关口（TS 依赖 JS 对象天然可序列化）；
  6. `_USE_LAST_ENTRY` sentinel；
  7. `SessionContext.model` 为 tuple（无生产消费方）；
  8. `agentDir` 参数 → `NOVA_AGENT_DIR` 环境变量；
  9. `complete_summarization` 的 `isawaitable` 分支与 `builtin_models()` 新建（无模块级状态原则）；
  10. `get_message_from_entry` 统一公共函数（比 TS 更整洁）；
  11. 消息扩展机制（CustomAgentMessage 基类 vs declaration merging）；
  12. `generate_id` 碰撞回退 16 hex（TS 完整 UUID）；
  13. `_read_session_header` 无 512 字节截断（更健壮）；
  14. `append_message` 附赠 pydantic 运行时校验（更严格，红利）。
- **➖ 不适用**：`migrateV1ToV2/V2ToV3/migrateSessionEntries`（第一版无历史格式）；`ReadonlySessionManager` 机械对应（用 SessionManagerProtocol 替代）；Python 独有导出 `is_valid_session_file` / `get_last_activity_time` / `estimate_messages_tokens`。
- **❌ 缺失**：无。

## 第六轮复核（2026-07-20，重读双方全部源码）

新发现 7 项差异，**已全部修复**（nova_harness 915 测试全绿，新增 2 个锁定测试）：

1. ~~JSONL 落盘格式不逐字节一致~~ → 已对齐：`entry_to_json` 模块级函数，紧凑分隔符 + `exclude_none=True`（fork_from 同样走该函数）。
2. ~~`_parse_timestamp` 抛 ValueError~~ → 已对齐 TS 静默语义：非法字符串回退 0（Python int 无 NaN）。
3. ~~`convert_to_llm` 返回注解偏窄~~ → 已放宽为 `List[Message]`。
4. ~~null-content 修复策略~~ → 已对齐 TS"保留 entry 修 content"：加载关口失败重试路径 `_repair_null_message_content`，正常行零开销；有测试锁定。
5. ~~`estimate_tokens` image 注释自相矛盾~~ → 已清理为 `ESTIMATED_IMAGE_CHARS`。
6. ~~`prepare_compaction` 空消息短路位置~~ → 已前移到 fileOps 提取之前（与 TS 同序）。
7. ~~`SessionInfo.model_validator` 死代码~~ → 已删。

## 第七轮终审决定（2026-07-20）

对剩余差异中三项可动项的最终处置（nova_harness 916 测试全绿，新增 1 个锁定测试）：

1. **fork 丢未知条目边界** → 已修：`fork_from` 改**行级原文复制**（合法 JSON 行含未知类型条目原样保留，非法行丢弃），磁盘层与 TS fork 零丢失；不加 UnknownEntry，类型层零侵入。
2. **`GenerateBranchSummaryOptions.signal` 可选** → 已改必填，对齐 TS 契约（生产调用方本就恒传）。
3. **`generate_id` 碰撞回退 16 hex** → 决定不动：回退路径现实不触发，功能与完整 UUID 等价。
