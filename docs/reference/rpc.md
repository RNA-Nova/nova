# RPC 契约

前后端线上协议：JSON-RPC 2.0 over stdio（NDJSON，每行一帧）。本文档是**概念与语义**参考；机器可读的完整契约由后端构建期导出：

- `packages/nova-tui/protocol/nova-wire.schema.json`——事件/条目/方法形状的 JSON Schema（后端构建期导出的落点）；
- `packages/nova-tui/src/protocol/nova-wire.gen.ts`——TS 类型 + 契约版本常量（生成物，勿手改）。

两者由 pytest 漂移测试保鲜（改协议忘了重新导出 = 测试红）。

## 版本语义

```
CONTRACT_VERSION_MAJOR.MINOR   （当前 1.3）
```

- **major 不等硬拒**：前端连上握手即比对，不兼容直接报"请对齐后端/前端版本"；
- **minor 加法放行**：新增方法/字段 bump minor，老前端连新后端安全（未知方法调了收到 `-32601`，未知字段被忽略）。

## 连接生命周期

1. 前端 spawn `nova-server`（缺省 rpc 模式）；
2. `initialize` → 返回服务器版本 + 契约版本 + **能力位**（真实方法表）；
3. 事件门：握手完成前前端不收 `agent/event`；
4. 收尸：stdin EOF → 服务器自行退出（退出码 0）——前端进程死掉不会留孤儿后端。

## 方法域（76 个方法，八域）

| 域 | 覆盖 |
|----|------|
| `session` | 会话生命周期（prompt/steer/followUp/队列/重试/树导航/克隆/导出导入/syncSession） |
| `model` | 模型发现/切换/scoped 池/thinking 级别 |
| `auth` | 鉴权状态/login/logout/runtime key |
| `resources` | skills/prompt templates/agents/personas 查询 |
| `settings` | 设置读写（无会话也可用） |
| `system` | 命令目录/扩展 flags/快捷键目录与回调 |
| `user_tools` | 用户工具 |
| `package` | pkgList/pkgInstall/pkgUninstall/pkgUpdate/pkgCheckUpdates |

自由负载方法（`Dict[str, Any]` 注解）不声明形状；有形状的方法签名即契约（params/result 模型集中在 `methods/shapes.py`）。

## 事件与同步

- `agent/event` 信封带 `seq`（服务器生命周期单调）+ `ts` + `sessionId`；
- **`syncSession`**：状态快照 + 条目分页 + 事件高水位一帧原子拿齐；之后只收 `seq > 水位` 的增量——不重不漏（旧前端无此方法时自动回退 getSessionState + getSessionEntries 两发路径）；
- 条目分页：`getSessionEntries(offset, limit)`。

## 反向原语（后端 → 前端）

后端运行中需要用户输入时经 `ui/request` 发起、前端经 `ui/response` 应答；`ui/cancel` 撤销悬空请求。前端连接后由 `system/capabilities` 上报支持的原语子集，未支持的方法后端自动降级（基线词汇 `select/confirm/input/form/notify` 由官方 nova-base 定义；包可自定义 `dialog:*` 等扩展词汇）。

多连接语义：请求按连接寻址（发起方优先，无归属广播首响应胜出）；`cancelRequest` 按连接隔离（`(connId, reqId)` 复合键）。

## 背压与保护

- 每连接在飞 handler 上限 256：超限对请求回 `-32004 overloaded`、对通知丢弃；
- 错误码约定：`-32601` 方法不存在 / `-32602` 参数校验失败 / `-32001` 会话未找到 / `-32004` 过载；
- 服务器内置事件循环滞后探针（>100ms 漂移记 `rpc-stderr.log`）。
