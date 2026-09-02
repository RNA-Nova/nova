# 资源与权限体系重构设计（四层漏斗终态）

> **状态：已实施（2026-08）**。验证：harness 1434 / bundle 395 / nova_agent 110 /
> nova-client 325 + 前端 178 全绿；PTY 矩阵 45/45；全栈冒烟通过。
> 施工阶段的遗留项：footer 展示 persona override（前端 status 槽位另案）；
> `parse_package_source_spec` 缺 user_tools 键（既有缺口，在案）。
>
> 本文是 2026-08 系列讨论的定案汇总：名单代数、四层漏斗、manager 版图、
> settings 权限粒度、persona 升格、subagent 概念消灭。施工按文末阶段执行。
>
> 对照实现：`pi/packages/coding-agent/src/core/`（pi 侧）、
> `nova_harness/src/nova_harness/core/`（本侧）。

---

## 1. 核心模型：四层漏斗 + 权限链

```
manifest（作者硬裁：除名，settings 不可复活）
  → trust（安全硬裁：项目源发现期整体排除）
    → settings（用户终裁：四级 pattern + 附加路径）
      → 注册表（碰撞裁决 project > user > package，first-wins + 诊断）
        → agent.yaml（角色软选配：只能收窄；是运行时状态的初始值）
          → SDK/CLI allow/exclude（宿主信任边界，硬闸，永远硬）
            → 运行时（会话 delta：面板绝对集，会话条目持久化）
              → 暴露给模型的清单
```

**权限链语义**（每层一句话）：

| 层 | 权力 | 性质 |
|---|---|---|
| manifest（`[tool.nova]`） | 作者的硬裁：`!` 除名不随包收集 | 不可复活（用户显式加路径是"另行安装"，不算复活） |
| trust | 安全的硬裁：不信任的项目源不进世界 | 发现期生效 |
| settings | 用户的终裁：对进了世界的资源生杀予夺 | 四级 pattern |
| agent.yaml | 角色的软选配：在用户终裁后的池子里挑 | 只能收窄，绝无强制开启权 |
| SDK/CLI | 宿主的硬闸：部署环境的能力红线 | 与 role_boundary 正交，永远硬 |
| 运行时 | 会话的临时开关：激活集 delta | 只收窄注册表，条目持久化、分支安全 |

**`+`/`-` 强制级的真实语义**（订正后）：pi 的强制级是**单名单内**"宽排除 + 精确豁免"
的支撑（`!icons/*` + `+icons/important`），不是跨层覆盖机制；manifest 除名的资源
pi 的用户同样复活不了。两边语义一致。

## 2. 名单代数：一种语法，两个作用域

- **路径级**（收集层）：`core/package/resolve/discovery.py::apply_patterns`——
  plain/`!`/`+`/`-` 四级，glob（pathspec）+ 精确强制级，pi 逐行对位。**已建成**。
- **名字级**（注册表层）：`core/utils/name_sets.py`（本次新建）——同一语法打在
  注册名上（精确匹配；名字空间平且小，glob 无的放矢）。三态：缺席=不设防 /
  空=全禁 / 名单；`!` 排除；纯排除名单 = 全放减排除。
- 纪律：两实现语义逐条对照（同用例双跑），不合并为一个函数（作用域不同）。
- **零命中诊断**：pattern 拼错静默无命中是 pi 的坑，我们做零命中警告（反超点）。

身份模型对照：pi 资源身份=路径（去重由文件系统白送，碰撞推迟到注册期）；
我们=注册名（收集期 first-wins + collision 诊断 upfront，下游规则零歧义）。

## 3. agent.yaml / AgentConfig 重新定性

- **AgentConfig = 运行时选配状态的初始值**（不是配置管理层）；选配状态唯一
  （注册池 + 激活集两层），三个写入者：初始化（漏斗序：发现→trust→manifest→
  settings→yaml→SDK）、运行时修改（面板等）、持久化（yaml 文件 / 会话条目）。
- 五个名单字段三态化（`tools`/`extensions`/`user_tools`/`commands`/`skills`，
  `Optional`：缺席=全放、空=全禁、支持 `!`）。
- **删除 `subagents` 死字段**（解析后无人消费；概念已被"都是 agents"取代）。
- 补 `source_info`（resolver provenance 透传）——七类资源来源跟踪齐平。
- `sections` 不再加载期固化：AgentConfig 只存 `persona:` 原始条目，
  装配移到 PersonaManager（按名引用必须等注册表就绪）。
- 文本类资源（skills/prompts/personas）yaml **仅裁包内**（"随时可加性"——
  用户/项目库不受角色作者管辖，settings 在上游也救不回）；能力类全量可见。
- **CapabilitySelection 报告**：每个 yaml 点名项的状态——`ok` / `missing` /
  `disabled_by_settings`（`disabled_by_manifest` 预留，机制无消费者）；
  两个检查时机：`nova-pkg validate`（静态，作者错误）+ 会话构建（运行时，
  环境差异），经既有诊断管线透出。

## 4. persona：素材 → 资源（升格）

- `[tool.nova]` 新增 `personas` 类目；三源发现（包 + `~/.nova/agent/personas` +
  `.nova/personas`）+ trust 门控 + `source_info`；命名 = 相对路径去扩展名
  （`coding/core`、`subagents/scout`）。
- **PersonaManager（新）**：装配（路径引用→读文件+包根收敛校验；注册名→
  注册表查找）+ override 旋钮（内存态）。
- `/persona` 运行时切换器：前端选择器（name + source 标签，首项"角色默认装配"），
  `persona_override` 会话条目持久化（tool-panel 管道复用）；只换身份文本，
  能力面不动；角色切换时 override 保留并可见（footer 显示）。
- 与 prompts 的边界：prompts = 用户命令宏（`/refactor` 宏展开，零提示词占用）；
  persona = 角色身份文本（装配进系统提示词）；skills = 模型自主能力说明书
  （菜单注入 + 按需加载）。**概念不合并**，发现管线同源。
- yaml 与 persona 不是一对一：yaml ⇄ AgentConfig 才是一对一；persona 是
  装配输入（一对多、可无、可共享）。

## 5. settings 层补齐与写入权限粒度

新增键：`tools` / `user_tools`（名字 pattern，全四级）、`personas`
（路径 + pattern，同 skills 形态）。已有 `extensions`/`skills`/`prompts`/`agents`
（路径 + pattern，resolver 已接线）。`disabled_commands` 保留（commands 是
扩展/skill/prompt 的运行时投影，不是资源——唯一投影级例外）。

**写入权限矩阵**：

| 写入者 | 用户偏好 | 资源管控 | 包管理 | 安全策略 |
|---|---|---|---|---|
| 前端（用户） | ✅ 通用写口（updateSettings） | ✅ 意图级 RPC（本次建） | ✅ 意图级 RPC（pkg*，已有） | ✅ /trust 链路 |
| 包扩展（ctx action） | ❌ | ⚠️ 仅增量动词（加/撤单条 pattern，仅全局层） | ❌ | ❌ |
| nova-pkg CLI | ❌ | ❌ | ✅ | ❌ |
| 域 manager / 工具 | ❌ | ❌ | ❌ | ❌ |

要点：通用写口只剩用户偏好项；凡有副作用链（写后需重解析/重装）的键一律
意图级 RPC；扩展只拿增量动词（可枚举、可审计、可精确撤销），不得整键覆盖。

## 6. manager 层终版版图

纪律：**读自由、写独占（一层一个 store）、manager 互不调用、复合效果编排
归 AgentSession、用户意图仅两门（RPC methods / 扩展 ctx actions）**。

| manager | 旋钮 | 职责 |
|---|---|---|
| **AgentManager（新）** | 当前角色 | agents 注册表视图、默认解析（显式>首个>base_agent）、可委派视图/菜单注入数据、CapabilitySelection 汇集、**yaml 写回**（见下） |
| ToolsManager | 激活集 | 裁决单点化（散闸→name_sets 单函数） |
| **PersonaManager（新）** | persona override | 装配 + 按名解析（从 agent_config loader 乔迁） |
| UserToolManager / SkillManager | —（无旋钮） | 只读视图 |
| SystemPromptManager | —（卸下 change_agent） | 纯渲染（config + 各 manager → 文本） |
| SettingsManager | — | settings.json 唯一写门（类型化 setter） |

**角色持久化闭环**：yaml（初始值）→ 会话调优（会话条目 delta）→
`/agent save` / `/agent save-as <name>`（物化回初始化文件）→ 下次直接命中。
**写入红线：包内 yaml 不可写**——包来源的角色影子写到 `~/.nova/agent/agents/<name>.yaml`
（user > package 优先级使影子生效）；user/project 级就地写回。

**`role_boundary` 设置**：`open`（默认，创造态——面板可见 settings 终裁后的
全池，yaml = 初始激活集）/ `strict`（yaml 硬边界，面板只见角色内）。
委派子会话天然硬（无面板无运行时修改者），worker 防递归天花板不受影响。
本质是 yaml `tools` 字段的语义开关，外在表现为面板可视/可操作范围。

## 7. subagent 工具重设计（消灭 subagents 概念）

- **只有 agents，没有 subagents**——工具名保留 subagent 仅指"委派"动作；
  消费会话已加载的 AgentConfig 注册表，**删除工具侧三源发现、`agent_scope`
  参数、独立 trust 判定**（重复管线归零）。
- **执行前确认框**（自治权检查点，非注入安全）：per-agent 名、会话级 always
  （`append_entry` 持久化、分支安全）、headless 放行（有 UI 时的增强，不是
  headless 新门槛）。跨会话"永远"不做——那是 trust.json 换名。
- **动态菜单注入**：激活工具含 subagent 时系统提示词渲染可委派菜单
  （name + description + source），模型不再靠报错学名单（反超 pi）。
- 三模式（single/parallel/chain）引擎不动；委派合作模式（持久子会话 +
  消息收发）留门不修路（结果带子会话 id 即可）。

## 8. SDK/CLI 层

- SDK：`CreateAgentSessionOptions.tools`/`exclude_tools`（已有）。
- CLI：`nova-harness run` 补 `--tools` / `--exclude-tools` 旗标（纯投影进
  options，CLI 无独有逻辑——SDK 和 CLI 是同一层）。
- 无 `noTools`：框架零内置工具，默认集恒为空，pi 那个参数是内置设计的债。

## 9. 后续独立任务（本轮不做）

- **设置界面**：一个面板两个数据源（后端 settings 经 RPC + 前端
  ui-settings.json），资源管控项成组进面板。
- **目录归并**：`packages/nova-harness/{backend,frontend}` 伞目录
  （现 nova_harness → backend，现 nova-client → frontend；import 名不变；
  纯搬家变更集，在逻辑重构之后单独做，不与语义变更混合）。

## 10. 明确不做（触发条件记录在案）

- skills/prompts 概念合并（调用方向相反：用户宏 vs 模型自主；SKILL.md 是
  外部标准）；发现管线同源即可。
- manifest 作者默认关 / `disabled_by_manifest`（无消费者；`enabled` 字段已就位）。
- 前端 UI 资产的 settings 开关（nova-client 管线另案）。
- 委派合作模式、名字 glob（命名族出现时再说）。

---

## 施工阶段

1. **名字级代数地基**：`name_sets.py`（三态 + `!`，四前缀预留）+ 与
   `apply_patterns` 的对照语义测试 + 零命中诊断。
2. **AgentConfig/yaml**：五字段三态 Optional 化 + `!` 支持；删 `subagents`；
   补 `source_info`；`sections` 改存 persona 原始条目。
3. **persona 升格**：`[tool.nova]` personas 类目 + 三源发现 + trust +
   SourceInfo；PersonaManager（装配乔迁 + override）；`/persona` 旋钮 +
   会话条目。
4. **settings 补齐**：`tools`/`user_tools`/`personas` 三键 + 意图级 RPC
   （资源管控动词，增量语义）。
5. **过滤点统一**：ToolsManager 散闸换代数单点；extensions/user_tools/
   commands/skills 过滤统一；CapabilitySelection 报告产出；`role_boundary`
   开关（open/strict）。
6. **AgentManager + subagent 重设计**：AgentManager（旋钮乔迁、菜单视图、
   save/save-as）；subagent 工具注册表化 + 确认框 + 菜单注入。
7. **bundle/文档/测试**：agents/*.yaml 同步、AGENTS.md（根+包级）、对比
   文档标已实施、CHANGELOG；测试重写；harness/bundle/nova_agent pytest +
   nova-client 构建测试 + PTY 矩阵全量验证。
