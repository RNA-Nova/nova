# Nova vs pi：AbortController 作用域家族对比

> 取消信号的家族盘点：两家都不是"一个全局 signal"，而是**每个可取消域一个
> controller**。本文对清两家的域划分、级联语义与 Esc 路由。
> 系列文档：`nova_vs_pi_api_ctx.md` / `nova_vs_pi_extension_events.md` /
> `nova_vs_pi_ctx_families.md`。

## 一、域清单对照

| 可取消域 | pi | nova | 对位 |
|---|---|---|---|
| **run**（LLM 流 + 工具执行） | run 级 controller（agent.ts:474，每次 run 创建） | `agent._abort_controller`（agent.py:505，每次 run 创建） | ✅ 同款 |
| 手动压缩 | `_compactionAbortController` | `_compaction_abort_controller` | ✅ |
| 自动压缩 | `_autoCompactionAbortController` | `_auto_compaction_abort_controller` | ✅ |
| 分支摘要 | `_branchSummaryAbortController` | `_branch_summary_abort_controller` | ✅ |
| 自动重试 | `_retryAbortController` | `_retry_abort_event`（controller 当 event 用） | ✅ |
| 用户工具（bash） | `_bashAbortController`（单个） | 每次活跃调用一个 `AbortController(f"user_tool:{name}")`（活跃表逐个跟踪） | ✅ nova 更细（并发多调用各自可停） |
| 启动模型刷新（15s 超时） | —（model-runtime.ts:155 调用级） | `services.py` 一次性 controller | ≈ |
| **UI 询问**（反向原语） | ❌ 无（对话框在前端本地，不挂 signal） | **`ScopedUIContext` 织入 run signal**（ui/cancel 撤销帧） | nova 独有——双进程拓扑必需 |
| OAuth 登录流程 | 每流程一个 `manualAbort`（组件持有） | 经 UIAuthInteraction 的 signal（对话框生命周期） | ≈ |
| UI 组件级（loader/selector refresh 等） | 有（BorderedLoader、model-selector 等） | 无（组件状态即取消） | pi 侧细节 |

**结论**：会话域一一对应；nova 多出"UI 询问域"（跨进程拓扑的必然产物），pi 多出若干前端组件级小域（本地语义，无需对齐）。

## 二、级联语义：`abort()` 停哪些

**pi**（agent-session.ts:1530）：

```ts
async abort(): Promise<void> {
    this.abortRetry();      // 停 retry
    this.agent.abort();     // 停 run
    await this.waitForIdle();
}
```

**nova**（agent.py:1528）：

```python
async def abort(self) -> None:
    self.abort_retry()
    self.abort_compaction()      # 手动压缩
    self.abort_branch_summary()  # 分支摘要
    self.abort_user_tool()       # 用户工具
    self.agent.abort()           # run
    await self.agent.wait_for_idle()
```

**nova 的级联更宽**：压缩/分支摘要/用户工具也随 abort 停。pi 把这几个域留给**Esc 动态路由**（onEscape 按当前状态选择停哪个：streaming→run、bash→bash、compaction→compaction）。

语义取舍：

- pi：`abort()` 是"停 loop"的窄语义，其他域由 Esc 路由单独停；
- nova：`abort()` 是"全部停"的宽语义——RPC 客户端调一次 abort 就清场（跨进程下前端没有"域级 Esc 路由"的信息，宽级联是更安全的默认）。

## 三、Esc 路由

| | pi | nova |
|---|---|---|
| 对话框开着 | Esc → 对话框本地取消（焦点路由，run 继续） | 同款（`DialogController.isActive` 让路给焦点组件） |
| 编辑器/流式 | Esc → abort run | 同款 |
| bash 用户工具运行中 | Esc → abortBash（独立域） | 当前仍走 abort run（级联覆盖 user tool） |
| 压缩/重试中 | Esc → 停对应 controller | 当前走 abort run（级联覆盖） |

nova 的宽级联让"域级 Esc 路由"暂时不必要——一次 abort 全停，语义简单可预期；pi 的窄级联 + 动态路由更精细（只停正在跑的那个域），是后续可抛光项。

## 四、两家共同的底层模式

1. **每域一个 controller**，从无全局 signal——pi 手写一族，nova 同构；
2. **scope 即职责边界**：controller 的存活期 = 域的执行期（run 开始建、结束清；调用开始建、结束清）；
3. **协作式取消**：signal 检查点语义（不是任务强杀），取消是"请停"不是"强杀"；
4. **nova 独有的跨进程一环**：UI 询问必须织入 run signal（ScopedUIContext）+ 撤销帧（ui/cancel）——pi 单进程不需要这一环（对话框本地焦点消化 Esc）。

## 五、结论

域划分两家几乎一一对应（pi 实践验证了这个划分）；nova 的多一个"UI 询问域"（分布式拓扑必需）和更宽的 abort 级联（跨进程客户端的更安全默认）。Esc 语义已对齐两级路由；域级动态路由（只停正在跑的域）列为抛光挂账。
