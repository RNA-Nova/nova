# cancelRequest 设计方案——RPC 级调用取消（OAuth 登录可取消化）

> 本文档面向不熟悉本仓库的读者，自包含地解释：为什么需要 `cancelRequest`、
> 现状链路长什么样、问题断在哪、为什么是 cancelRequest 而不是别的方案、
> 以及每一行要改什么。所有关键论断都标注了代码位置（文件:行号），可逐条核对。

---

## 1. 背景：一个 15 分钟无法取消的登录

### 1.1 用户故事

用户在 nova TUI 里输入 `/login` 想登录 kimi：

1. 后端走 device code 授权流程，前端显示"打开浏览器授权，等待中…"；
2. 用户改变主意（或发现登错了账号），按下 **Esc**；
3. **什么都没发生**——后端的轮询循环继续跑，最长 15 分钟
   （`_DEVICE_CODE_TIMEOUT_SECONDS = 15 * 60`，
   `packages/nova_ai/src/nova_ai/auth/oauth/kimi.py:45`）。

更糟的是：如果用户"放弃"之后随手在浏览器里完成了授权，credential 会被
**实际存盘**——一个用户以为已经取消的登录悄悄成功了。

### 1.2 参照系：pi 是怎么做的

pi（`/Users/liujinming/agent/pi`，单进程 TypeScript 架构）里这是流畅的：

- 登录对话框组件 `LoginDialogComponent` 自己持有一个 `AbortController`
  （`packages/coding-agent/src/modes/interactive/components/login-dialog.ts:15`）；
- 用户按 Esc → 组件 `cancel()` → `abortController.abort()`
  （login-dialog.ts:83-91）；
- 这个 signal 被直接传进 OAuth 流程内部
  （`interactive-mode.ts:5226`：`loginProvider` 把 `dialog.signal` 传给
  `modelRuntime.login({signal, ...})`），**device code 轮询本身被立即打断**。

pi 能做到这一点，是因为它的 UI 组件和 OAuth 流程活在**同一个进程**里：
`AbortController` 是一个内存对象，直接从组件手里递到流程手里。

### 1.3 nova 的处境

nova 是跨进程架构：

```
┌─────────────────────┐   JSON-RPC over stdio   ┌──────────────────────┐
│  nova-tui（Node）    │ ◀────────────────────▶ │  nova_harness（Python）│
│  对话框、Esc 键位     │                        │  OAuth 轮询、abort     │
└─────────────────────┘                        └──────────────────────┘
```

UI 组件在前端进程，`AbortController` 是内存对象，**过不去进程边界**。
所以"组件持有 controller，Esc 就地 abort"这个 pi 模式无法直接照抄。

本文档要解决的问题：**在跨进程约束下，让 `/login` 的 Esc 取消达到与 pi
相同的体验（立即打断轮询），且方案本身在架构上是干净的、可推广的。**

---

## 2. 现状链路解剖：`/login` 从按键到轮询的完整路径

要理解方案，先完整看清现在发生了什么。以下每一环都已读过代码确认。

### 2.1 前端发出 prompt 调用

用户输入 `/login` 并回车。TUI 的编辑器提交处理
（`packages/nova-tui/src/app.ts:127-140`）：

```ts
this.editor.onSubmit = (text) => {
  const trimmed = text.trim();
  // ...
  void this.runtime.prompt(trimmed).catch((error) => this.showError(error));
};
```

slash 命令不是特殊通道——它就是一条普通 prompt 文本。`runtime.prompt`
（`packages/nova-harness/frontend/src/runtime.ts:234-236`）薄转发到
`client.call('prompt', {text})`，得到**一个挂着的 Promise**：直到后端这次
调用完成才会 settle。

`client.call`（`packages/nova-harness/frontend/src/wire/client.ts:182-201`）的
结构：每次调用分配一个自增数字 id（`const id = ++this.nextId`），把
`{resolve, reject}` 存进 `pending: Map<id, ...>`，写入
`{jsonrpc:'2.0', id, method:'prompt', params}` 帧。应答帧按 id 配对
（client.ts:225-234）。

**注意**：这个 Promise 没有任何取消手段——`call` 返回的是裸 Promise。

### 2.2 后端一消息一 task

后端 RPC 服务器（`packages/nova-harness/backend/src/nova_harness/rpc/server.py`）
的读循环为**每条入站消息开一个独立 asyncio task**（server.py:110-112）：

```python
task = asyncio.create_task(self._handle_raw(raw))
self._pending_tasks.add(task)
task.add_done_callback(self._pending_tasks.discard)
```

设计意图（注释在 server.py:108-109）：长 prompt 不阻塞后续的 abort/steer
命令进入方法表。`_pending_tasks` 是一个 **set**——只能批量遍历（服务器
关闭时全部 cancel，server.py:115-118），**不能按 request id 寻址**。

`_handle_raw`（server.py:138-152）解析消息后调
`await self._methods.dispatch(msg)`，拿到应答后写回。

### 2.3 slash 命令在 prompt task 里同步执行

`session.prompt()` 的第一步就是识别扩展命令
（`packages/nova-harness/backend/src/nova_harness/core/agent_session/agent.py:1141-1145`）：

```python
if expand_prompt_templates and current_text.startswith("/"):
    if await self._slash_handler.execute_command(current_text):
        # ...
        return
```

`execute_command` → `_execute_command`
（`controllers/slash_input.py:101-120`）里 `await command.handler(args, ctx)`
——**命令 handler 是同步 await 在 prompt 调用栈里的**。

也就是说：用户输入 `/login` 的那一刻起，整条调用链是：

```
前端 client.call('prompt') 的 Promise 挂着
  └─ 后端 server 的某个 task：_handle_raw
       └─ dispatch → prompt 方法（methods/session.py:170-180）
            └─ await session.prompt("/login")
                 └─ agent.py:1142 await slash_handler.execute_command
                      └─ slash_input.py:112 await /login 的 handler(args, ctx)
                           └─ session_commands.py:277 await ctx.model_runtime.login(...)
                                └─ OAuth device code 轮询（最长 15 分钟）
```

**关键结论**：轮询期间，prompt 这个 RPC 调用的 task 一直活着。谁取消这个
task，谁就能打断轮询。

### 2.4 轮询本身是可中断的（signal 通道已存在）

nova_ai 的 device code 轮询**早就支持 signal 取消**：

- `poll_oauth_device_code_flow` 的 `DeviceCodePollOptions` 有 `signal` 字段
  （`packages/nova_ai/src/nova_ai/auth/oauth/device_code.py:43`）；
- kimi 登录入口从 interaction 对象上取 signal：
  `kimi.py:290-291`：

  ```python
  async def _login(interaction: AuthInteraction) -> OAuthCredential:
      return await _login_device_code(interaction, getattr(interaction, "signal", None))
  ```

  这是一个**鸭子类型约定**：interaction 对象如果碰巧有 `signal` 属性，
  就会被拿去传给轮询（kimi.py:285）。

- 而 `UIAuthInteraction`（harness 的登录交互桥，
  `core/config/auth/interaction.py:33-35`）恰好把构造参数存为
  `self.signal`。

所以"signal 通道"在设计上是铺好的。但它现在**流不动**，原因见下。

### 2.5 断点一：signal 的源头是 None

`/login` handler 构造 UIAuthInteraction 的地方
（`packages/nova_coding_agent/extensions/session_commands.py:277-279`）：

```python
credential = await ctx.model_runtime.login(
    provider, "oauth", UIAuthInteraction(ctx.ui, ctx.get_signal())
)
```

`ctx.get_signal()` 是什么？扩展上下文的 signal 来自
`context_actions.get_signal`，其定义（agent.py:818-819）：

```python
def get_signal() -> Optional[Any]:
    return getattr(self.agent, "signal", None)
```

`agent.signal` 是**当前 run** 的 abort signal——只有 agent loop 在跑
（LLM 对话）时才存在。而 `/login` 是一个扩展命令，**根本不在 run 里**：
没有 `agent_start`，没有 run signal。所以 `get_signal()` 返回 `None`，
铺好的 signal 通道里流的是空。

### 2.6 断点二：前端没有取消入口

轮询等待期间，前端收到的是 `device_code` 通知——经 `UIAuthInteraction.notify`
（interaction.py:60-71）走 `ui.notify("notify", {...})` 单向通道，TUI 的
`DialogController.showNotice`（`components/dialogs/controller.ts:153-158`）
把它显示在**状态槽位**——一条文本，不是对话框，不抢焦点。

TUI 的 Esc 路由（app.ts:204-218）：

```ts
if (matchesKey(data, 'escape')) {
  if (this.dialogs.isActive) return undefined;      // 四件套对话框开着 → 归对话框
  const status = this.runtime.store.status;
  if (status === 'working')  { /* abort run */ }
  if (status === 'retrying') { /* abortRetry */ }
  if (status === 'compacting') { /* abortCompaction */ }
  // idle → 什么都不做
}
```

轮询期间：四件套对话框没开（notify 不是 request），会话状态是 **idle**
（slash 命令不走 agent loop，没有 `agent_start` 事件）。**Esc 落在
idle 分支——什么都不做。** 用户连一个可以按的地方都没有。

### 2.7 断点三（隐藏）：task 取消会在传输层留下泄漏

即使我们有了取消 task 的手段，还有一个更隐蔽的问题。
`TransportUIContext.request`（后端向前端发对话框的通道，
`rpc/protocol/ui_context.py:65-122`）的结构是：

```python
try:
    await self._transport.write(payload)        # 发 ui/request 帧
    abort_task = asyncio.create_task(_watch_abort())  # signal 竞速
    result = await asyncio.wait_for(future, timeout=timeout)
    return self._normalize_response(result)
except asyncio.TimeoutError:
    return UIResponse(cancelled=True)
finally:
    if abort_task is not None:
        abort_task.cancel()
    self._pending.pop(request_id, None)
```

它有 signal 竞速（abort → 发 `ui/cancel` 撤销帧），有超时兜底，
**但没有 `asyncio.CancelledError` 分支**。如果宿主 task 被 cancel，
`await asyncio.wait_for(...)` 抛出 `CancelledError`，走 `finally` 清理了
后端状态——**但前端的对话框还开着**（没人给它发 `ui/cancel`），它将永远
挂在那里等一个永远不会来的应答。

这意味着：实现 task 取消之前，必须先补这个收尾，否则取消 api_key 登录
（它正等着用户输入密码框）会把前端框弄成僵尸。

### 2.8 现状小结：三个断点

| # | 断点 | 位置 | 后果 |
|---|------|------|------|
| 1 | `/login` 不在 run 里，`get_signal()` 返回 `None` | agent.py:818 | signal 通道流空，轮询不可断 |
| 2 | 轮询期间前端只有一条通知文本，Esc 路由无分支 | app.ts:204 | 用户没有取消入口 |
| 3 | `TransportUIContext.request` 无 `CancelledError` 收尾 | ui_context.py:111 | task 取消会留下僵尸对话框 |

---

## 3. 候选方案对比与选择

### 方案 A：照抄 pi（组件持有 AbortController）——不可行

`AbortController` 是内存对象，跨不了进程。前端组件的 controller 和后端
轮询之间没有任何传递介质。放弃。

### 方案 B：并发等待框（应用层编排）

`/login` handler 里自己 `new AbortController()` 传给 `UIAuthInteraction`，
然后**并发**跑两个协程：login 任务 + 一个"等待授权"对话框 request
（`ui.request`），谁先完成听谁的（`asyncio.wait(FIRST_COMPLETED)`）。
用户 Esc 关框 → request 返回 cancelled → handler `controller.abort()`
→ 轮询断。

可行，但有暗伤：`AuthInteraction` 接口允许流程在任意时刻调 `prompt`
（比如 callback server 模式要求用户粘贴回调码）。等待框 request 在飞时，
流程自己又发一个 prompt request——**两个对话框叠加**，前端
DialogController 一次只该有一个焦点框。kimi/codex 恰好是纯轮询流程
（只 notify 不 prompt）所以不会触发，但框架能力不能建立在"碰巧"上。

### 方案 C：双侧 signal + 专用上行取消帧

前端组件持有本地 `AbortController`，abort 后经一条**新发明的上行取消帧**
（带"流程 id"）通知后端取消。

方向正确（这是分布式取消的标准形态），但要发明两个新概念：**流程 id**
（两侧 signal 的配对标识）和**取消帧的幂等规则**。协议复杂度高。

### 方案 D：cancelRequest（RPC 级调用取消）——**选中**

关键洞察：`/login` 的执行**本身就是一个 RPC 调用**——前端
`client.call('prompt')` 分配的那个 request id，就是这个"流程"的天然标识。
**不需要发明流程 id，它已经存在。**

LSP（Language Server Protocol）十年前就为 JSON-RPC 设计了这件事：
`$/cancelRequest {id}`——按 request id 寻址，取消正在执行的调用。

落到我们的架构：

```
前端：client.call('prompt') → id=42，Promise 挂着
用户按 Esc（等待框上）
  → 前端本地立即关框（零延迟）
  → 前端发 cancelRequest {id: 42}
后端：server 查 id→task 映射 → task.cancel()
  → CancelledError 在轮询的 asyncio.sleep 处抛出
  → 沿 await 链穿透 login → handler → slash_input → session.prompt
  → server 给 id=42 写回 cancelled 应答（code -32800）
前端：Promise 以"已取消"收尾 → 显示"登录已取消"
```

**为什么 D 最优**：

1. **不发明任何新概念**。request id 是现成的；task 是现成的
   （server.py:110）；task.cancel 的协作式取消语义是 asyncio 自带的——
   `CancelledError` 在每个 `await` 点抛出，轮询的 `asyncio.sleep`、
   HTTP 请求全都是 await 点，天然可断，**连 signal 透传都用不上**。
2. **异常吞不掉**。`CancelledError` 继承 `BaseException`（Python 3.8+），
   `slash_input.py:113` 和 `session_commands.py:283` 的 `except Exception`
   都捕不到它——取消不会被误报成"登录失败"，也不会被静默吞掉。
3. **通用化白捡**。任何长命令（将来的 `/install` 大包、慢速扩展命令）
   自动可取消，不用每个场景重新发明一次"等待框 + signal"编排。
4. **headless 纯度**。取消不依赖任何 UI 存在——任何 RPC 客户端都能发起，
   契合"取消属于流程、UI 只是触发器"的概念模型。
5. **填补取消世界观的最后一块**。三类取消各自走自己的通道，没有平行
   宇宙：

   | 取消对象 | 通道 | 现状 |
   |---|---|---|
   | run / 域（retry、compaction） | 领域方法 `abort*` | ✅ 已有 |
   | UI 交互（四件套对话框） | request 通道自带（`ui/cancel` 下行 + `cancelled` 应答上行） | ✅ 已有 |
   | 长 RPC 调用（命令执行） | RPC 通道自带（`cancelRequest`） | ❌ 本方案补上 |

**与 pi 的关系**：前端等待框组件依然持有本地取消能力（Esc → 本地立即
关框，不等后端往返），pi 模式的内聚性保留；跨进程同步经 cancelRequest
完成。双侧各自一等，各管各的世界。

### 一个明确的设计决策：做成"方法"而不是 LSP 式"通知"

LSP 的 `$/cancelRequest` 是通知（notification，无应答），语义"尽力而为"。
我们选择做成**普通 RPC 方法**（有应答 `{ok, cancelled}`）：

- 前端能知道取消是否命中（`cancelled: false` = 调用已完成或 id 不存在，
  幂等）；
- 方法表、能力位、schema 导出、TS 类型生成**全部走现有机制，零新增**
  （昨天加 `abortCompaction` 已验证这条路径：注册即进 schema）；
- 通知反而要在 `_handle_ui_inbound`（server.py:154）那种特判里开新类别。

---

## 4. 全局时序图

```
用户                nova-tui            ui-runtime client        nova_harness server            OAuth 轮询
 │                    │                      │                          │                            │
 │ 输入 /login 回车    │                      │                          │                            │
 │───────────────────▶│ promptCancellable    │                          │                            │
 │                    │─────────────────────▶│ {id:42, method:prompt}   │                            │
 │                    │                      │─────────────────────────▶│ task_A = create_task       │
 │                    │                      │                          │  (登记 request_tasks[42])  │
 │                    │                      │                          │  └─ session.prompt         │
 │                    │                      │                          │     └─ /login handler      │
 │                    │                      │      ui/notify           │        └─ login            │
 │                    │ ◀────────────────────────────────────────────────│           └─ 请求设备码 ──▶│
 │                    │ {type:"auth", url,    │                          │                            │─ 轮询开始
 │                    │  userCode, ...}       │                          │                            │  (sleep 循环)
 │ ◀─ 等待框弹出 ──────│                      │                          │                            │
 │ （URL + 授权码      │                      │                          │                            │
 │   + Esc 取消提示）   │                      │                          │                            │
 │                    │                      │                          │                            │
 │ 按 Esc             │                      │                          │                            │
 │───────────────────▶│ ① 本地立即关框         │                          │                            │
 │                    │ ② cancel()           │                          │                            │
 │                    │─────────────────────▶│ cancelRequest {id:42}    │                            │
 │                    │                      │─────────────────────────▶│ 查映射 → task_A.cancel()   │
 │                    │                      │                          │         CancelledError ───▶│─ sleep 处抛出
 │                    │                      │   {jsonrpc应答,           │◀─ 沿 await 链穿透 ─────────│
 │                    │                      │    error:{code:-32800}}  │  （request_tasks[42] 清理） │
 │ ◀─ "登录已取消" ────│ ◀─ promise reject ───│                          │                            │
 │   （本地显示）       │   (AbortError)       │                          │                            │
```

成功路径不变：轮询拿到 token → credential 存盘 → handler 发"登录成功"
消息（经事件流进转录区）→ prompt 应答到达 → 前端关框清句柄。

---

## 5. 详细实施

### 5.1 harness（后端 Python）

#### ① ServerState 加 request_tasks 映射

**文件**：`rpc/protocol/methods/state.py`

现状：ServerState 是方法 handler 的依赖容器（`state.runtime`、
`state.ui_context` 等）。新增：

```python
# 在飞 RPC 调用的 id → 执行 task 映射（cancelRequest 的寻址基础）。
# server 读循环登记/清理，cancelRequest 方法查询。
request_tasks: Dict[Any, asyncio.Task]
```

**为什么放这里**：方法 handler 的签名是 `(params) -> result`，依赖只能经
`state` 注入；而登记/清理发生在 server 的 `_handle_raw`。放 state 上，
server 写、方法读，无循环依赖。

#### ② server.py：登记映射 + 取消时的应答写回

**文件**：`rpc/server.py` 的 `_handle_raw`（当前 138-152 行）

```python
async def _handle_raw(self, raw: Dict[str, Any]) -> None:
    try:
        msg = parse_message(raw)
    except JSONRPCError as exc:
        if raw.get("id") is not None:
            await self._write(build_error(raw["id"], exc).to_dict())
        return

    if self._handle_ui_inbound(msg):
        return

    # cancelRequest 寻址基础：登记 id → 当前 task
    request_id = msg.id
    if request_id is not None:
        self._state.request_tasks[request_id] = asyncio.current_task()
    try:
        response = await self._methods.dispatch(msg)
        if response is not None:
            await self._write(response.to_dict())
    except asyncio.CancelledError:
        # 被 cancelRequest 取消：必须写回应答，否则前端 Promise 永远悬挂。
        # code 对齐 LSP RequestCancelled（-32800）。
        if request_id is not None:
            err = JSONRPCError(-32800, "Request cancelled")
            await self._write(build_error(request_id, err).to_dict())
        raise  # 保持 task 的 cancelled 语义（不吞 CancelledError）
    finally:
        if request_id is not None:
            self._state.request_tasks.pop(request_id, None)
```

**要点**：
- `msg.id` 非 None 才登记（通知没有 id，无从取消）；
- `except CancelledError` 里写应答后 **re-raise**——吞掉它会让 task 以
  "正常完成"收场，破坏 asyncio 的取消语义；
- `finally` 里清理映射——无论成功、失败还是被取消，映射不留尸体。

#### ③ cancelRequest 方法

**文件**：`rpc/protocol/methods/system.py`（传输层操作归 system 域，
与 initialize 等同域）

```python
async def cancelRequest(params: Dict[str, Any]) -> Dict[str, Any]:
    """按 RPC request id 取消正在执行的调用（LSP $/cancelRequest 的方法版）。

    幂等：id 不存在或已完成 → cancelled=False（不视为错误）。
    """
    target_id = params.get("id")
    task = state.request_tasks.get(target_id)
    if task is None or task.done():
        return {"ok": True, "cancelled": False}
    task.cancel()
    return {"ok": True, "cancelled": True}
```

注册进方法表（params 模型 `CancelRequestParams {id: int}`，result 模型
`CancelRequestResult {ok: bool, cancelled: bool}`，写入 shapes.py）。
方法面 63 → 64，能力位自动覆盖。

**幂等的意义**：用户连按两次 Esc、或 Esc 的同时调用刚好完成——第二次/
后到的那次 cancel 查不到活 task，回 `cancelled: false`，不是错误。

#### ④ TransportUIContext 补 CancelledError 收尾（修断点三）

**文件**：`rpc/protocol/ui_context.py` 的 `request`（当前 111-122 行）

```python
try:
    await self._transport.write(payload)
    if signal is not None:
        abort_task = asyncio.create_task(_watch_abort())
    result = await asyncio.wait_for(future, timeout=timeout)
    return self._normalize_response(result)
except asyncio.TimeoutError:
    return UIResponse(cancelled=True)
except asyncio.CancelledError:
    # 宿主 task 被 cancel（如 cancelRequest）：撤销前端对话框，
    # 否则前端框永远挂着等一个不会来的应答（僵尸框）。
    await self._send_cancel(request_id)
    raise
finally:
    if abort_task is not None:
        abort_task.cancel()
    self._pending.pop(request_id, None)
```

**场景**：api_key 登录正在等用户输入密码框时，用户在前端按 Esc（等待框
或别的入口）触发 cancelRequest → handler task 被 cancel → 它在
`wait_for(future)` 处抛 CancelledError → 这里补发 `ui/cancel` → 前端
密码框关闭（DialogController.dismiss 已有，controller.ts:147-151）→
链路干净。

#### ⑤ UIAuthInteraction 发结构化 auth 通知（修断点二的另一半）

**文件**：`core/config/auth/interaction.py`

现状：`_notify` 只发 `{message, type: "info"/"progress"}` 纯文本
（interaction.py:84-86），前端只能显示一条状态文本。

改动：`device_code` / `auth_url` 事件改发**结构化载荷**：

```python
self.ui.notify("notify", {
    "message": message,          # 保留人类可读文本（headless/日志兜底）
    "type": "auth",              # 新标记：前端据此开授权等待框
    "url": event.verificationUriComplete or event.verificationUri,
    "userCode": event.userCode,  # device_code 事件携带
})
```

**向后兼容**：旧的 `type: "info"` 消费者（TUI 的 `showNotice` 按 message
显示文本，controller.ts:153-158）不受影响——`type: "auth"` 只是新增分支。

**headless 行为不变**：NoOpUIContext 下 notify 静默丢弃；headless 本来就
无法完成交互式授权，15 分钟超时兜底——与今天一致。

#### ⑥ 契约版本与工件

- `schema_export.py:56`：`CONTRACT_VERSION_MINOR` 1.0 → 1.1（加法语义：
  新方法，无破坏性变更）；
- 重跑导出：
  `python -m nova_harness.rpc.protocol.schema_export`（写
  `packages/nova-harness/frontend/protocol/nova-wire.schema.json` 与
  `src/protocol/nova-wire.gen.ts`——`NovaWireMethodMap` 自动多出
  `"cancelRequest"` 条目）；
- 漂移测试 `tests/core/rpc/protocol/test_schema_export.py:108` 的方法
  计数 63 → 64。

### 5.2 nova-client（Node 层）

#### ⑦ client.callCancellable

**文件**：`packages/nova-harness/frontend/src/wire/client.ts`

现状 `call`（182-201 行）返回裸 Promise，不可取消。新增（**不改 `call`**
——现有 ~60 个方法转发全部不动）：

```ts
/** 可取消调用：返回 promise 与 cancel 句柄。 */
callCancellable<M extends NovaWireMethod>(
  method: M,
  params?: WireParams<M>,
): { promise: Promise<WireResult<M>>; cancel: () => void } {
  if (!this.child?.stdin) throw new Error('后端连接未建立');
  const id = ++this.nextId;
  const promise = new Promise<WireResult<M>>((resolve, reject) => {
    this.pending.set(id, { resolve, reject });
    this.child!.stdin!.write(
      JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n',
    );
  });
  const cancel = () => {
    // 幂等：settle 后 pending 已删，再 cancel 是静默空转
    if (!this.pending.delete(id)) return;
    // ① 本地立即收尾（不等后端往返——Esc 体验零延迟）
    //    AbortError 命名对齐 Web 平台取消语义
    const error = new Error('调用已取消');
    error.name = 'AbortError';
    // 注意：pending.delete 先删了，reject 要用删前拿到的条目——
    // 实现时先取条目再删（细节，代码里处理）。
    // ② 通知后端取消执行（幂等：已完成则 cancelled:false）
    this.send({ jsonrpc: '2.0', method: 'cancelRequest', params: { id } });
    // ……reject(error) 在删条目前取出后调用
  };
  return { promise, cancel };
}
```

**幂等安全性**：cancel 后后端的 cancelled 应答（或正常应答）到达时，
`pending` 里已没有该 id——client.ts:227 的 `if (!pending) return` 静默
丢弃，天然幂等。

#### ⑧ runtime.promptCancellable

**文件**：`packages/nova-harness/frontend/src/runtime.ts`（命令 API 区）

```ts
/** slash 命令专用：可取消的 prompt 调用（普通对话请用 prompt + abort）。 */
promptCancellable(text: string): { promise: Promise<unknown>; cancel: () => void } {
  return this.client.callCancellable('prompt', { text });
}
```

### 5.3 nova-tui（前端）

#### ⑨ AuthWaitingDialog 组件

**文件**：新建 `packages/nova-tui/src/components/dialogs/auth-waiting.ts`

复刻 pi `LoginDialogComponent` 的三态展示（login-dialog.ts:96-131、
207-212），作为 pi-tui 组件组装：

- `showAuth(url)`：OSC8 hyperlink（Cmd/Ctrl+click 可打开）+ 提示文本；
- `showDeviceCode(url, userCode)`：hyperlink + `Enter code: XXXX` 醒目行；
- `showWaiting()`：`Waiting for authentication... (Esc to cancel)`；
- 底部常驻 `(Esc to cancel)` 提示（pi 的 keyHint 对位）。

组件**持有取消回调**（构造注入 `onCancel: () => void`），Esc/取消键位
触发：本地关框 + 调 onCancel。这就是 pi 的 `AbortController` 内聚模式
在跨进程下的对位——组件自己管自己的取消语义。

#### ⑩ app.ts：slash 命令走 promptCancellable + 等待框接线

**文件**：`packages/nova-tui/src/app.ts`

a) `onSubmit` 分流（当前 127-140 行）：

```ts
this.editor.onSubmit = (text) => {
  const trimmed = text.trim();
  if (!trimmed) return;
  this.editor.addToHistory(trimmed);
  if (trimmed.startsWith('!')) { /* bash 用户工具，不变 */ return; }
  if (trimmed.startsWith('/')) {
    // slash 命令：可取消调用（OAuth 登录等长命令的 Esc 入口）
    const { promise, cancel } = this.runtime.promptCancellable(trimmed);
    this.pendingCommandCancel = cancel;
    promise
      .catch((error) => {
        if (error?.name === 'AbortError') {
          this.showInfo('已取消');   // 本地提示（后端不会发消息——CancelledError 不穿 except Exception）
        } else {
          this.showError(error);
        }
      })
      .finally(() => {
        this.pendingCommandCancel = undefined;
        this.authDialog?.close();    // 无论成功/失败/取消：关等待框
        this.authDialog = undefined;
      });
    return;
  }
  void this.runtime.prompt(trimmed).catch((error) => this.showError(error));
};
```

**为什么普通对话不走 promptCancellable**：普通 prompt 的取消语义是
**abort run**（领域操作：停流式、收尾卡片、复位状态机），不是"取消这次
调用"。双通道做同一件事是混乱源——Esc 路由的 working 分支继续用
`runtime.abort()`，不变。

b) auth 通知接线（DialogController 的 notice 分支或 app 级）：
`notice.params.type === 'auth'` 时，new `AuthWaitingDialog`（注入
`() => this.pendingCommandCancel?.()`），按载荷调 showAuth/showDeviceCode
+ showWaiting，替换编辑器槽位（与四件套同一模式）。

c) Esc 路由加分支（当前 204-218 行），优先级：**四件套 > auth 等待框 >
状态域级路由**：

```ts
if (matchesKey(data, 'escape')) {
  if (this.dialogs.isActive) return undefined;     // 四件套优先（不变）
  if (this.authDialog) {                            // auth 等待框其次
    this.authDialog.cancel();                       // 本地关框 + cancelRequest
    return { consume: true };
  }
  const status = this.runtime.store.status;         // 状态路由（不变）
  // ...
}
```

---

## 6. 边界情况与异常路径

| 场景 | 路径 | 结果 |
|---|---|---|
| Esc 时登录刚好完成 | cancelRequest 查映射：task 已 done → `cancelled:false` | 幂等，不算错误；前端框已被 promise settle 关闭 |
| 连按两次 Esc | 第一次 cancel 后 `pendingCommandCancel` 清掉；第二次无句柄 | 静默空转 |
| api_key 登录（非 OAuth）被 Esc | task cancel → TransportUIContext 的 CancelledError 分支（改动④）发 `ui/cancel` → 密码框关闭 | 无僵尸框 |
| 取消时正在请求设备码（HTTP 在飞） | CancelledError 在 HTTP 的 await 处抛出 | 连接由 asyncio 清理；轮询未开始 |
| 后端进程死亡 | client.handleExit（client.ts:207-214）reject 全部在飞调用 | 既有行为，不变 |
| headless（无 UI）跑 /login | notify 丢弃；无等待框无 Esc；15 分钟超时兜底 | 与今天一致 |
| cancelRequest 一个不存在/非法的 id | 映射查不到 → `cancelled:false` | 幂等应答 |
| 取消后用户又在浏览器完成授权 | 轮询已死，不会再取 token | credential **不会**落盘——状态不再撒谎 |

---

## 7. 测试计划

### harness（pytest）

1. `cancelRequest` 方法级（照 `test_session_methods.py` 的 FakeSession
   模式）：
   - 取消进行中的长调用 → `{ok: true, cancelled: true}`；
   - 取消不存在的 id → `{ok: true, cancelled: false}`（幂等）；
   - 缺 `id` 参数 → params 校验报错。
2. server 级（`NovaServer` + memory transport）：
   - `_handle_raw` 登记/清理映射（调用完成后 `request_tasks` 无残留）；
   - task 被 cancel 后前端收到 `code: -32800` 的错误应答（Promise 不悬挂）。
3. `TransportUIContext.request` 的 CancelledError 收尾：
   - 宿主 task 被 cancel → 发出 `ui/cancel` 帧 + CancelledError 继续传播。
4. 端到端（harness 内）：起一个挂住的假命令 handler（`await asyncio.sleep(300)`），
   cancelRequest → handler 的 task 死、调用方收到 cancelled 应答。

### ui-runtime（node:test）

5. `callCancellable`：cancel → 本地 promise 以 AbortError reject + 写出
   `cancelRequest {id}` 帧；settle 后再 cancel → 静默（幂等）。

### 真实冒烟（pty）

6. `/login` 走 kimi device code：等待框出现（URL + 授权码）→ Esc →
   框立即关、后端轮询立即断（日志/进程确认无后续轮询请求）、显示
   "已取消"；
7. 再跑一遍完整授权（浏览器确认）→ 登录成功、credential 落盘、
   模型列表刷新——成功路径零回归；
8. api_key 登录（输入框）Esc → 框关、无僵尸。

### 全量回归

9. `nova_harness` / `nova_agent` / `nova_coding_agent` 非集成测试全绿；
10. `nova-client` npm test 全绿 + `nova-tui`/`nova-client` tsc
    build 通过。

---

## 8. 文档与入账

- `examples/rpc_capabilities.md`：补 `cancelRequest` 方法说明与
  `type:"auth"` 通知约定（该文档是 RPC 能力面的对外说明，需同步）；
- 根 `CHANGELOG.md` `[Unreleased]` 入账；
- `AGENTS.md`：若 RPC 方法面/取消体系的描述涉及，则同步（当前无方法
  计数类描述，预计只需确认）。

---

## 9. 明确不做（防范围蔓延）

1. **普通对话 prompt 不改 cancellable**。run 的取消走 `abort`（域级清理
   更完整），`cancelRequest` 只服务命令执行路径。两个通道语义不同：
   `cancelRequest` = "我不要这个调用的结果了"（传输层）；`abort*` =
   "取消这个领域活动"（领域层）。文档写死这条界限。
2. **LSP 的 `$/cancelRequest` 通知形式不做**。方法形式的应答
   （`cancelled: true/false`）让幂等确认成为可能，且白拿方法表/能力位/
   schema 导出。
3. **nova_ai 的 `interaction.signal` 鸭子约定不拆**（kimi.py:291）。
   它作为库级能力自洽（nova_ai 独立使用时没有 task 概念）；本方案让
   主路径绕开它，而不是依赖拆它。
4. **不发明流程 id、上行取消通知帧**。RPC request id 就是流程 id；
   将来若出现"无 RPC 调用在飞的后台流程需要前端取消"（M4 之后的形态），
   再议上行取消帧——现在加是投机。
5. **`AuthInteraction` 基类不强制显式 signal 构造参数**。主路径改为
   task cancel 后，signal 不再是关键路径，不值得动 nova_ai 的公共接口。

---

## 10. 附录：关键代码事实表（全部亲读验证）

| 事实 | 位置 |
|---|---|
| server 一消息一 task，`_pending_tasks` 是无寻址能力的 set | `rpc/server.py:110-112, 49` |
| 服务器关闭时已批量 cancel task（取消语义已在用） | `rpc/server.py:115-118` |
| `_handle_raw` 现行结构（parse → ui 特判 → dispatch → 写应答） | `rpc/server.py:138-152` |
| slash 命令在 prompt 调用栈内同步 await | `core/agent_session/agent.py:1141-1145` → `controllers/slash_input.py:112` |
| `get_signal()` 返回 run 级 signal，命令态为 None | `core/agent_session/agent.py:818-819` |
| 轮询支持 signal 取消 | `nova_ai/auth/oauth/device_code.py:43` |
| kimi 经鸭子约定取 interaction.signal | `nova_ai/auth/oauth/kimi.py:290-291` |
| device code 超时 15 分钟 | `nova_ai/auth/oauth/kimi.py:45`、`openai_codex.py:46` |
| `UIAuthInteraction(ui, signal)` 存 self.signal；notify 发纯文本 | `core/config/auth/interaction.py:33-35, 84-86` |
| `/login` handler 的 `except Exception` 捕不到 CancelledError | `nova_coding_agent/extensions/session_commands.py:280-285` |
| `TransportUIContext.request`：signal 竞速有、CancelledError 无 | `rpc/protocol/ui_context.py:65-122` |
| 前端 `client.call` 无取消句柄；pending 按 id 配对；无条目静默丢弃 | `nova-client/src/wire/client.ts:182-201, 225-234` |
| 反向通道三帧（ui/request、ui/notify、ui/cancel）路由齐全 | `nova-client/src/wire/bridge.ts:90-124` |
| DialogController：四件套 + dismiss（ui/cancel 消费端）+ isActive | `nova-tui/src/components/dialogs/controller.ts:43-175` |
| TUI Esc 路由现状（四件套 > working/retrying/compacting） | `nova-tui/src/app.ts:204-218` |
| slash 命令经 runtime.prompt 发出 | `nova-tui/src/app.ts:139` |
| schema 方法表自动收集（注册即进导出） | `tests/core/rpc/protocol/test_schema_export.py:102-120` |
| pi 参照：组件持有 AbortController + Esc cancel + signal 传 login | `pi/.../login-dialog.ts:15,83-91`、`interactive-mode.ts:5220-5230` |
