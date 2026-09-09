# memory/ — Agent 长期记忆库（Obsidian vault 兼容）

这是本项目的 Agent 记忆系统（纯文件 I/O）。对 Obsidian：把整个项目根目录（或单独 `memory/`）作为 vault 打开即可，INDEX.md 里的 Dataview 表格会自动汇总（需安装 Dataview 插件；不装也不影响手动链接）。

## 分层

| 层 | 目录 | 内容 | 写入时机 |
|---|---|---|---|
| 情景记忆 | `tasks/` | 每次任务一页索引：输入、参数、结果、产物链接、异常摘要 | 任务完成后 |
| 语义记忆（工程） | `learnings/` | 从任务中蒸馏的坑与经验（带规避规则） | 遇到新坑/重要决策时 |
| 程序记忆 | `playbooks/` | 可复用的执行流程（含前置检查清单） | 流程新增/变更时 |
| 发现 | `discoveries/` | 任务数据中的新见解（带证据、置信度、验证状态） | 发现可推广的结论时 |
| 假设待验 | `hypotheses/` | 可实验检验的预测（含验证方案与判定标准） | 从发现/报告中拆出实验建议时 |
| 文献知识 | `literature/` | 文献中提炼的跨任务可复用规则/事实 | 文献调研后 |
| 实体卡片 | `entities/` | 任务对象的稳定领域知识（编号映射、关键参数） | 引入新对象时 |
| 模板 | `_templates/` | 各类笔记的模板 | 复制使用 |
| 系统 | `system/` | 模式运行时数据（skill 绑定、步骤 schema） | 由 experiment 模式自动维护 |

## 给 Agent 的使用规则

1. **任务前**：先读 `INDEX.md` → 相关 playbook 与 learnings，再动手；
2. **任务后**：按本页末尾的"写入约定"蒸馏记忆；
3. frontmatter 字段固定（Dataview 依赖），id 递增（learn-NNN / pb-NNN）；
4. 链接一律用相对路径 `[[]]` 双链，保证 vault 内可跳转；
5. 记忆只写**可复用的结论与规则**，流水账留在任务产物目录里。

## 写入约定

- 情景记忆页（`tasks/<task_id>.md`）由 experiment 模式自动生成并登记到 INDEX；
- learnings / playbooks / discoveries 等由任务结束后蒸馏写入，使用 `_templates/` 下对应模板；
- 过时的记忆标记 `status: obsolete`，不直接删除。

## 给人类的使用建议

- 检索：Obsidian 内置搜索或 INDEX 的 Dataview 表；
- 审阅：定期合并相似 learnings，把过时的标记 `status: obsolete`；
- 图谱视图可直观看到 task ↔ learning ↔ playbook 的关联网络。
