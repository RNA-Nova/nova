# Agent 组合声明与人格

**三层模型**：`backend/` 与 `frontend/` 是素材海（工具/扩展/人格文本/模板），`agents/*.yaml` 是**组合层**——一个 agent 一份纯选配声明，零内容附着。

## agents/*.yaml

```yaml
# agents/coding_agent.yaml —— name 缺省 = 文件名
name: coding_agent
version: "1.0.0"
description: Nova coding agent with local file system tools and subagent delegation
author: nova

# 人格默认模型（可选）：初始模型解析链的一档（CLI/scoped 之后、settings 之前）；
# 无鉴权/未知 provider 静默落回，不会自动强占会话默认模型
model: volcengine/deepseek-v4-pro-260425

# 人格文本组装（顺序即组装顺序）。条目能相对本 yaml 解析为文件/目录的按路径
# 装配（文件直读；目录递归收 .md 按相对路径字典序在该位置展开；路径须收敛
# 包根内），否则按注册名查 persona 注册表
persona:
  - ../backend/personas/coding/core.md
  - coding/extra          # 注册名形态（persona 注册表）

# 能力名单（统一三态：键缺席=全放；[]=全禁；非空=名单，支持 ! 排除）
tools:            # 工具激活集（字符串，或 {name, description} 覆盖提示词简介）
  - read
  - write
  - bash
  - subagent      # 启用后本 agent 可委派任务给其他 agent
extensions:       # 扩展白名单（空=全允许）
  - permission_gate
user_tools: []    # 显式空=全禁
commands:         # slash 命令允许集（空=全放）
  - tree
  - fork
skills:           # 只裁包内 skill；用户级/项目级始终放行
  - python_best_practices
```

### 关键语义

- **只有 agents，没有 subagents**：可委派名单 = 会话注册表全量，无主从划分；`subagents` 字段已删除（防递归靠被委派方不含 `subagent` 工具）；
- **tools 的 `role_boundary`**：`open`（默认）只做初始激活集，用户运行中可开更多；`strict` 连注册表一起裁（角色硬边界）；
- **过滤链**：denylist → settings allowlist → agent yaml 白名单，三关依次收窄；
- **运行时切换**：`/agent <name>` 切角色 = 角色初始态全量重建 + `session_start` 重放（每角色权利平等）；`/agent save[-as]` 把当前生效状态物化回 yaml（包来源影子写 user 级）。

## persona（人格）

persona 是**身份文本**资源（区别于 `prompts/` 的用户命令宏）：

```
backend/personas/
├── coding/core.md            # 注册名 coding/core
├── coding/guide.md           #       coding/guide
└── subagents/worker.md       #       subagents/worker
```

- 命名 = 相对 personas 根去 `.md`；目录条目递归收 `.md`；
- 三源发现：包 `personas` 类目 + `~/.nova/agent/backend/personas/` + `.nova/backend/personas/`；
- 装配归会话期 PersonaManager：路径引用（相对 agent yaml，收敛包根）与注册名混排；
- 运行时切换：`/persona <name|default>`——内存态覆盖 + 分支持久化（换分支各自恢复）。

写人格文本的建议：身份与原则在前（模型权重高），工具使用守则在后；**别写工具名单**（名单归 yaml 的 `tools:`，文本里写会漂移）。

## prompts（用户模板）

`backend/prompts/*.md`——用户经 `/模板名` 触发的命令宏；`$@` 是参数占位符：

```markdown
<!-- backend/prompts/review.md -->
请评审以下改动，关注正确性与可维护性：

$@
```

用户输入 `/review src/auth.ts` → 模板展开后作为用户消息发出。工作流模板（如官方 implement.md = scout→planner→worker 编排说明）也在这一类。

## skills

`backend/skills/<name>/SKILL.md`——模型可自动调用的技能（frontmatter 控 `disable_model_invocation`）。当 agent 激活工具含 `read` 时，可用 skill 清单以 XML 注入系统提示词末尾；`skills:` 名单只裁**包内** skill（用户级/项目级始终放行——用户技能库不需 agent 作者授权）。

## 子代理（subagent 工具）协作

激活 `subagent` 工具的 agent 可把任务委派给注册表里的任何 agent：

- 工具按名查表（未知名报错并列出可用名 + 来源标签）；
- 系统提示词自动注入 `# Available Agents` 菜单（模型不靠报错学名单）；
- 执行前确认归扩展（官方 `subagent_gate`：逐名裁决 允许一次/本会话始终允许/取消）；
- 三模式：single / parallel（≤8 任务、并发 4）/ chain（`{previous}` 占位符串接）。

给你的 agent 写 subagent 四件套（侦察/规划/执行/评审）是官方推荐拓扑——参考 `bundles/nova_coding_agent/agents/` 的 scout/planner/worker/reviewer 声明。

下一页：[分发与发布](distribution.md)。
