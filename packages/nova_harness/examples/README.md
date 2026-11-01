# nova_harness 示例与现行设计文档

本目录是**现行设计文档**（8 篇，随代码演进保持刷新）+ 可运行示例。
旧的落地图纸与对比审计快照已于 2026-10 清理删除（历史见 git 记录）。

## 现行设计文档（8）

| 文件 | 主题 |
|------|------|
| `nova_architecture_2.0.md` | 三层模型总纲（已定型设计共识，文首含 WS 归 Python 的翻案修订记录） |
| `nova_architecture_2.1.md` | 架构 2.0 落地后的完整描述：harness 内部 → 线上协议 → Node 层与前端 |
| `rpc_capabilities.md` | RPC 能力清单与评估（76 方法 + 2 直管消息，nova_server 独立后第三轮定格） |
| `package-management.md` | 包管理设计路线、安装语义、目录布局、Resolver 与典型用法 |
| `executor-integration.md` | executor 接入与环境感知注入设计定案（bundle 执行后端切换的依据） |
| `user_tools_design.md` | 用户工具（Session Tool）架构设计 |
| `resource-permission-refactor.md` | 资源与权限体系（四层漏斗终态，已实施——被活代码与活文档引用） |
| `server-item-layer-design.md` | server 层设计（开放统一 item 层 + 归约归服务器——被 `nova_server/reduction/` 代码引用） |

## 可运行示例（非 CI 保鲜，接口以源码为准）

| 文件 | 主题 |
|------|------|
| `01-quickstart.ipynb` | 创建 `AgentSession`、发起对话、查看消息 |
| `event_subscriber.py` | 事件订阅辅助脚本 |
| `extensions/weather_extension.py` | 扩展示例 |

运行 Notebook 需配置 API Key（示例默认 Volcengine：`export VOLCENGINE_API_KEY=...`）：

```bash
cd packages/nova_harness
jupyter lab examples/
```
