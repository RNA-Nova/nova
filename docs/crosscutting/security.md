# Nova 安全注意事项

> 坐标：横切·安全（清单篇——事实清单非规范；升格为标准篇属【待补】。体系总图与状态三档约定见 `docs/README.md`）。


1. **API Key 存储**
   - `nova_harness` 的鉴权信息保存在 `~/.nova/agent/auth.json`，由 `AuthStorage` 管理。
   - `models.json` 中的 `api_key` / header 值按 TS 语义解析：`$VAR` / `${VAR}` 为环境变量引用（缺失即报错并指明变量名），`!cmd` 前缀执行 shell 命令取输出，`$$`/`$!` 转义，其余一律按字面量；解析逻辑在 `nova_harness/config/resolve.py`，鉴权在请求时经 nova_ai 的 auth 链完成，不写入 `Model` 对象。
   - `nova_ai` 层不持久化密钥，全部通过环境变量按 `provider` 名称映射读取。

2. **会话数据**
   - 会话历史以 **JSONL 明文**存储在 `~/.nova/agent/sessions/--<cwd>--/` 下，可能包含敏感代码片段或输出。
   - 根目录 `.gitignore` 已忽略 `sessions/` 与 `*.session`。

3. **文件操作安全**
   - Agent 组合声明加载器（`nova_harness/resources/loaders/agent_config.py`）只读取 agents 目录内的 `*.yaml` 组合声明及其相对引用的 persona 文件（包根收敛校验），不接受外部传入的任意路径，无路径逃逸面。
   - 扩展发现（`package/resolve/discovery.py`）只收集根级 `.py` 文件与合法扩展目录，不递归非扩展目录，避免辅助模块被当扩展加载执行。

4. **Project Trust**
   - `~/.nova/agent/trust.json` 保存用户对项目文件夹的信任决策；扩展可通过 `project_trust` 事件参与裁决。
   - 无 UI 的 headless/RPC 模式默认信任存在 `.nova` 资源的项目，以保持向后兼容；有 UI 的前端（如 `nova-client` 的 TUI 宿主）会弹出确认对话框。
   - **trust 只存在于运行时**：会话启动决议 + resolver 读取门控。`nova-pkg` 包管理不做 trust 检查（装/卸包是主动行为），也没有 `trust`/`untrust` 子命令。

5. **敏感信息**
   - 历史 notebook 文件 `packages/nova_agent/src/test.ipynb` 已删除。新增示例 `packages/nova_harness/examples/` 中不应包含真实 API Key。

6. **Secret 卫生**（codex 对位——值层脱敏 + 出口清洗）
   - **凭据模型值层脱敏**：`nova_protocol` 的 `RedactedString`（str 子类——repr 调试通道恒 `<redacted>`，str 使用通道即原值，序列化/落盘照常明文）；`OAuthCredential.access/refresh`、`ApiKeyCredential.key` 均用此类型，`ApiKeyCredential.env` 为 `repr=False`，`OAuthCredential.__repr_args__` 白名单只露 `type/expires/accountId`（`extra="allow"` 扩展字段可能藏第二个 token）。纪律：**凭据/令牌类字段一律 `RedactedString`，新的含密结构一律白名单 repr**——防"顺手打印模型/dump 产物/异常栈捕获"；`print(裸值)` 是有意取值的使用通道，不设防。
   - **出口统一清洗**：`redact_secrets`（Bearer/sk-/AKIA/api_key= 等词表，占位 `[REDACTED_SECRET]`）——终态住所归 `nova_ai/utils/sanitize.py`（迁移前在 `nova_protocol/sanitize.py`）；**只在出口调用**（当前接入点：会话 JSONL 落盘的 `entry_to_json` 单点），不在生产者内部调用；导出口径（`export_to_jsonl`）不经清洗（用户主动导出自己数据）。bundle 侧的审批弹窗/RPC 出栈渲染随整体优化接入。

---

