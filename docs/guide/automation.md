# 自动化与集成

TUI 之外，Nova 有两条无界面通道：**print 一次性执行**（脚本/CI）与 **RPC 服务器**（自写前端/集成）。

## print 模式（一次性执行）

```bash
# pip 渠道
nova-harness run coding_agent --task "给 src/ 写个 README" 
# 二进制形态
runtime/nova-server run coding_agent --task "…"
```

| 旗标 | 说明 |
|------|------|
| `agent` | 角色名（缺省用默认解析链） |
| `--task <文本>` | 任务内容（也可管道喂 stdin） |
| `--cwd <目录>` | 工作目录 |
| `--json` | 输出 JSONL 事件流（机器消费） |
| `--trust` | 信任项目目录（headless 缺省不信任 `.nova` 资源） |
| `--no-session` | 不落盘（临时会话） |
| `--skill <路径>` / `--prompt-template <路径>` | 临时加载 skill / 模板（可重复，不持久化） |
| `--tools, -t <逗号名单>` / `--exclude-tools, -xt <逗号名单>` | 工具白名单 / 排除集（排除集在白名单之后应用） |
| `--<扩展 flag>` | 扩展注册的命名开关透传（如 coding 包的 `--plan`：启动即只读规划）；`--name` 布尔开 / `--name=值` 传值，未注册名报错 |

退出码：0 成功；非 0 失败（模型错误/工具失败/中断）。CI 里直接用。

## RPC 模式（自写前端）

```bash
runtime/nova-server            # = nova-server rpc：JSON-RPC over stdio 服务器
```

- 传输：stdio NDJSON（每行一帧）；握手 `initialize` → 版本 + 契约版本（major/minor）+ 能力位（方法表）；
- 契约语义：major 不等硬拒、minor 加法放行——老前端连新后端安全；
- 方法域：session（会话/队列/重试/树）、model、auth、resources、settings、system、user_tools、package 八域 76 方法；
- 事件：`agent/event` 推送（带单调 seq——`syncSession` 原子快照 + 增量续传不重不漏）；
- 反向原语：`ui/request` / `ui/response`——后端运行中向你的前端要输入（确认/选择/表单），能力经 `system/capabilities` 协商，未支持的方法后端自动降级；
- 取消：`cancelRequest` 按连接隔离；stdin EOF 即收尸退出（适配 spawn 托管）。

协议细节与类型见 [RPC 契约](../reference/rpc.md)；线上 schema 由后端构建期导出（`protocol/nova-wire.schema.json` + TS 双工件）。

## 子代理自调

bundle 的 `subagent` 工具在打包形态下自调 `nova-server run`（冻结兼容通道）——你写自动化时也可以同样用子代理三模式（single/parallel/chain）做批量任务编排。

## 环境变量（自动化相关）

- `NOVA_OFFLINE=1`：离线运行（模型调用仍会出网——离线只管包/更新类动作）；
- `NOVA_AGENT_DIR`：隔离状态根（CI 每job一个临时根，互不污染）；
- `NOVA_SUBAGENT_MAX_CONCURRENCY`：子代理并发上限（默认 4）。
