# UI 原语体系：泛型 transport + 词汇包化

> 本文定稿后端（nova_harness）与前端（nova-client）之间**反向通道**
> 的最终架构。决策时间：2026-08（架构 2.0 收窄之后、M1 之前）。
>
> 一句话：**harness 只做泛型 transport（零词汇）；全部交互词汇
> （select/confirm/input/notify 及将来的富原语）是包数据，定义权归包作者。**

## 1. 分层与所有权

### harness 持有（冻结，零词汇）

泛型 `UIContext` 接口：

```python
class UIContext(Protocol):
    capabilities: Set[str]          # 前端宣告的 method 集合
    has_ui: bool                    # 有无前端挂在通道上（非 NoOp）
    async def request(method: str, params: dict) -> UIResponse
    def notify(method: str, params: dict) -> None
    def has_capability(method: str) -> bool
```

- `request` / `notify` 是反向通道的**完备最小集**——时序二分（要应答 / 不要
  应答）即消息交互的完备分类（对应 JSON-RPC 的 request/notification 两种帧）；
  内容全部下沉到 `method + params` 数据契约；
- `NoOpUIContext`（空能力集，print/SDK 默认降级）与 `TransportUIContext`
  （RPC 转发 + request_id/Future 配对 + 全局 300s 超时兜底）属 transport 层，
  留在 harness；
- harness **不持有任何 method 词汇**：`STANDARD_UI_METHODS`、各原语的
  params/response schema、Python 便捷方法（select/confirm/input）全部移出。

### 包持有（词汇全量）

官方 bundle（`nova_coding_agent`）定义标准词汇（基线五件套 + 将来的富原语）：

- **定义（数据）**：方法名 + params/result schema + 语义文档（包资源，被
  harness 发现/被前端读取）；
- **Python 糖库**：`select(ui, title, options)` 等便捷函数（包装
  `ui.request`，类型化返回值）——扩展作者 import 自 bundle；
- **第三方富原语的前端渲染器**：自定义原语（`ext:` 前缀）的 TS 组件在包
  `ui/` 段（M4 `dialog:*` slot 注册，挂账中——见 §6）。

> **form 的落地决策**：`form`（多字段表单）作为**第五件原生原语**落地
> （TUI 原生组件 `components/dialogs/form.ts`），而非走包 `ui/` 段——
> `dialog:*` slot 尚未落地，而 form 是足够通用的结构化输入原子
> （与四件套同级的体验一致性收益大于包化灵活性）。`dialog:*` slot 落地后
> 服务于第三方自定义原语，form 不迁回。

第三方包可定义自定义原语（建议 `ext:` 前缀命名空间防碰撞），经同一泛型
`ui/request` 通道，**harness 零改动**。

### 前端持有

- 基线五件套的对话框**原生实现**（select/confirm/input/notify + form——
  任何像样前端的本职）；
- 第三方包原语经 `dialog:*` slot 渲染（挂账）；
- **能力自动宣告**：capabilities = 原生基线 + 已加载包原语（M1/M4 落地）。

## 2. 为什么这样分（判据与依赖方向）

- **时序二分完备**：request/notify 覆盖"后端发起"的全部交互形态
  （要应答 / 不要应答）；前端发起的交互走 RPC 命令与事件上行，不属本通道；
- **函数不可过线**：传组件工厂的 UI（pi 的 custom/setFooter）物理上无法
  经 RPC——声明式归词汇包（数据可过线），自由代码归 TS 逃生舱（M4）；
- **依赖方向决定归属**：泛型 transport 被 harness 自身（auth/trust/扩展
  上下文/降级实现）消费，必须留 harness；词汇只被扩展与前端消费，归包；
- **能力协商即演进机制**：新原语上线 = 包更新 + 前端宣告，契约 minor 演进、
  未宣告即优雅降级，harness 不动。

## 3. 契约语义约定

- **`UIResponse`**：`{ value: Any, cancelled: bool, confirmed?: bool }`——
  `value` 承载任意负载（select→str、confirm→bool、form→答案集），解释权归
  method 定义者；`cancelled` 统一"取消/不支持/超时"语义；
- **超时**：传输层全局 300s 兜底；per-request 超时走 params 约定字段
  `timeout_ms`（前端对话框自解释，倒计时呈现归前端）；
- **取消**：调用方外部竞速（`request` 与调用方 signal 竞速，如
  `UIAuthInteraction`）；**abort 胜出后须撤销前端对话框**——协议补
  `ui/cancel {id}` 帧（M1 落地，pi `dialog.signal` 的对位物）；
- **断线**：transport close 时所有 pending request 立即 resolve 为
  cancelled（不做 300s 挂起）。

## 4. harness 内部消费的适配

harness 自身的流程改用泛型接口：

- `UIAuthInteraction`（OAuth 登录交互）：`ui.request("select", ...)` /
  `ui.request("input", ...)` + cancelled → `LoginCancelledError`；
- project trust 询问：`ui.request("confirm", ...)`；
- 扩展（permission_gate / session_commands）：import bundle 糖库。

## 5. 联动与工具 UI 的定位（定案）

- **联动三通道**：custom_type 条目渲染（M4 `entry:*`）/ 反向原语 /
  details 平铺；接头靠命名契约，前后端扩展永不相调；
- **工具 UI 定位（v3.1 后期判决）**：工具永远 Python 侧执行（反向工具
  通道已取消——Node 不执行后端能力）。交互式工具（执行中询问用户）经
  **反向原语**（`ui.request`：select/confirm/input）——permission_gate
  已验证该链路；执行前询问归扩展钩子（tool_call + ui），执行中澄清
  归对话回合。Python 工具保持"参数进、结果出 + 可询问"的执行器形态。

## 6. 里程碑挂账

- **M1**：基线四件套前端对话框；`ui/cancel` 撤销帧；断线 pending 收尾；
- **M1/M4**：`dialog:*` slot + 能力自动宣告；
- **M4**：逃生舱、`entry:*` 渲染器发现约定；
- **后 M1**：官方原语包落地 `form`（声明式富对话框）。
