# Nova 安全注意事项


1. **API Key 存储**
   - `nova_harness` 的鉴权信息保存在 `~/.nova/agent/auth.json`，由 `AuthStorage` 管理。
   - `models.json` 中的 `api_key` / header 值按 TS 语义解析：`$VAR` / `${VAR}` 为环境变量引用（缺失即报错并指明变量名），`!cmd` 前缀执行 shell 命令取输出，`$$`/`$!` 转义，其余一律按字面量；解析逻辑在 `nova_harness/config/resolve.py`，鉴权在请求时经 nova_ai 的 auth 链完成，不写入 `Model` 对象。
   - `nova_ai` 层不持久化密钥，全部通过环境变量按 `provider` 名称映射读取。

2. **会话数据**
   - 会话历史以 **JSONL 明文**存储在 `~/.nova/agent/sessions/--<cwd>--/` 下，可能包含敏感代码片段或输出。
   - 根目录 `.gitignore` 已忽略 `sessions/` 与 `*.session`。

3. **文件操作安全**
   - Agent 配置加载器（`nova_harness/resources/loaders/agent_config.py`）只读取 agent 目录内的固定文件（`agent.yaml`、`description.md`、`sections/*.md`），不接受外部传入的任意路径，无路径逃逸面。
   - 扩展发现（`package/resolve/discovery.py`）只收集根级 `.py` 文件与合法扩展目录，不递归非扩展目录，避免辅助模块被当扩展加载执行。

4. **Project Trust**
   - `~/.nova/agent/trust.json` 保存用户对项目文件夹的信任决策；扩展可通过 `project_trust` 事件参与裁决。
   - 无 UI 的 headless/RPC 模式默认信任存在 `.nova` 资源的项目，以保持向后兼容；有 UI 的前端（如 `nova-client` 的 TUI 宿主）会弹出确认对话框。
   - **trust 只存在于运行时**：会话启动决议 + resolver 读取门控。`nova-pkg` 包管理不做 trust 检查（装/卸包是主动行为），也没有 `trust`/`untrust` 子命令。

5. **敏感信息**
   - 历史 notebook 文件 `packages/nova_agent/src/test.ipynb` 已删除。新增示例 `packages/nova_harness/examples/` 中不应包含真实 API Key。

---

