# 前后端分离与扩展能力开放：定案汇总

> 本文汇总 2026-08 第二轮重构（继 `nova_harness/examples/resource-permission-refactor.md`
> 之后）的设计定案：扩展能力全量开放、UI 控制面对齐 pi、open/strict 角色边界、
> /agent 切换语义、OSC 8 链接边界、以及目录/状态分治设计。
>
> 状态标注：✅ 已实施 / 📐 已定案未实施 / ❌ 明确不做。

---

## 1. 前后端边界总原则

- **后端（Python）** = 逻辑、执行、事实、持久化；**前端（Node）** = 呈现、交互、宿主效果。
- **ctx 收录判据**：只收"后端够不着的宿主原语"（对话框/编辑器/剪贴板/setStatus/
  终端效果等）；后端方法的访问面 = `invoke`（生成的 NovaWireMethodMap 带类型、
  全量、零维护、随后端自动增长）。**不得为后端方法手写包装域**（双份维护 +
  开放幻觉——手写糖 API 曾加后撤销，教训在案）。
- 写死位置：`packages/nova-tui/src/presentation/extension-api.ts` 注释。

## 2. 扩展能力开放（✅ 已实施）

**后端开的（RPC，事实/持久化）**：

- `appendEntry(customType, data)`——任意包形态经 invoke 产生持久化 custom 条目
  （B 型纯 TS 包的 `registerEntryRenderer` 自此闭环）；方法表 74→75；
- `getSessionAgents` / `getPersonas` / `setPersonaOverride`；
- 快照加 `agentName` / `personaOverride`；`session_info_changed` 扩为三字段
  全量值（改名/换角色/换 persona 三处发射，前端直写）。

**前端开的（ctx 宿主原语）**：

| 原语 | 语义 | 备注 |
|---|---|---|
| `events.on(type, handler)` | 事件观察口（bus 只读桥，通配 `*`，包重载自动注销） | B 型包可做宿主同级响应式 UI；pi 无对应物 |
| `runInteractive(command)` | TUI 挂起/恢复终端让位 | 自 interactive-shell 对话框上移 |
| `setTitle(text)` | 终端标题覆盖（与宿主自动标题协调） | |
| `notifyDesktop(title, body)` | OSC 9/777/99 桌面通知 | pi 需手写 OSC，我们内建 |
| `setFooter(factory)` / `setHeader(factory)` | footer/启动区整件替换 | pi 对位；env 回灌宿主数据（git branch/扩展状态/快照/invoke）；异常回退默认渲染 |
| `setWorkingMessage` / `setWorkingIndicator` / `setWorkingVisible` | loader 三旋钮 | pi setWorking* 对位 |

**注册面新增**：`widgetBelow` 区域（pi setWidget 双 placement）、`registerCommand`
支持 `getArgumentCompletions`（slot 命令参数补全，pi 对位）。

**pi 对照结论**：对平全部；反超点 = events.on / runInteractive 正式原语 /
notifyDesktop 内建 / registerDialog / 工具渲染器独立覆盖（pi 须连执行体重注册）。
有意不抄：内建消息本体与选择器族不可替换（pi 同）。

## 3. /agent 与 /persona（✅ 已实施）

- **change_agent = 角色初始态全量重建 + `session_start(reason="agent_change")` 重放**：
  不携带旧角色激活集（携带击穿能力边界）；重放让扩展条目恢复对切换生效；
  agent 条目恢复在 agent_change 下跳过（防旧角色回切）。change_agent 改异步。
- **tool-panel 条目加角色标签**：恢复仅当条目角色匹配——组合出"每角色面板记忆"。
- **前端 `session_info_changed` → refreshSnapshot 全量对账**（切换后 activeTools
  快照不刷新的根因修复）。
- **footer 显示 `角色·persona · model · thinking`**；/persona = 运行时人格切换器
  （能力面不动，会话条目持久化）。

## 4. open/strict 角色边界（✅ 已实施）

- settings 键 `role_boundary`：**open（默认）**= yaml 只做初始激活集、面板可见全池；
  **strict** = yaml 是注册表硬闸门。设置面板 18 项含"角色边界"开关。
- 词汇定案：技术键值 open/strict 全链路一致，footer 不加牌子。
- 级联修复：placeholder 污染搜索值（SearchableSelector 改 PlaceholderInput 包装）；
  updateGlobalSettings 守卫只认 snake 导致**面板 camel 项持久化从未生效**
  （改 snake+alias 双收）；资源键写入自动 reload（键名归一化 `to_snake` 比对）。
- PTY：`scripts/pty-role-boundary.py` 全过。

## 5. 模型交互修复（✅ 已实施）

- `cycle_model` 补发 Bus 2 `ModelChangedEvent`（此前只发 Bus 3——ctrl+p "无效果"
  根因），`_emit_model_changed` 统一双发点；
- 直选未配置模型谎报"已切换" → 判读 `ok:false` 报"未配置凭据"；
- /model Tab：空池守卫 + 标题可变（`setTitle` 显示当前作用域）+ 池非空默认 scoped 档；
- ctrl+p 反馈（不足两个提示 + 成功通知）。
- PTY：`scripts/pty-model-matrix.py` 14 断言。

## 6. OSC 8 链接边界（✅ 已定案并实施）

- **可点**：assistant markdown 链接（pi-tui 内建，探测即发射）+ 登录授权 URL +
  裸文本 URL/`path:line`（终端自动探测）。
- **工具卡片刻意纯文本**：过程证据不链接（密集输出下 OSC 8 常驻下划线是噪音），
  可点性归 assistant 汇总层。曾全量实现后按"密度-价值"原则回退，
  `scripts/pty-links.py` 双向断言（markdown 必发射 + 工具卡片 file:// 零发射）。
- 责任划分：点击归终端（VS Code/iTerm 等），我们只管"可识别模式输出"；
  不支持 OSC 8 的终端静默退化纯文本，零回归、零能力分支。

## 7. CapabilitySelection 全资源化（✅ 已实施）

- 覆盖 tools/extensions/user_tools/commands/skills/personas 六类（agents 无选配）。
- 归因精度按身份域分级：名字级 settings（tools/user_tools/commands）四态全；
  路径级中名字可从路径机械推导的（extensions）可归因；名字锁在文件内容里的
  （skills）退化 ok/missing；personas 报 missing（装配失败归 PersonaManager 诊断）。
- 共享判定函数 `build_selection_report` 在 `name_sets.py`；各过滤点产出 →
  AgentSession 收集处（`_build_runtime` 重建）→ AgentManager provider 透出 →
  快照（非 ok 项）→ TUI 启动通知。
- 顺带修复既存 bug：首建时 yaml extensions 名单不生效（解析前移）。

## 8. skills / prompts 概念（❌ 不合并，保持现状）

- 判据：调用方向相反（prompt=用户宏零注入；skill=模型自主发现，索引注入系统
  提示词）；SKILL.md 是外部标准。
- kimi-code 先例在案（prompt 是 skill 的一种 `type`，单管线）——若将来合并，
  照其形态。触发条件：第一个包作者分不清该发哪个。

## 9. 目录与状态分治（✅ 已实施）

**目录终态**（镜像包三段式 + 宿主分级）：

```
~/.nova/agent/
├── settings.json / auth.json / trust.json / models.json   # 后端契约与安全态
├── sessions/                  # 会话状态（JSONL 分支树 + 条目）
├── packages/                  # 共享（后端装、前端只读）
├── logs/ / bin/               # 后端运行时
├── backend/                   # 后端散养资源（extensions/skills/prompts/personas）
├── agents/                    # 组合声明（两半共享）
└── frontend/                  # 前端域（按宿主分级）
    ├── tui/                   # settings.json / state/ / keybindings.json /
    │                          # themes/ / debug/ / tools/ / dialogs/ / index.ts
    └── web/                   # 预留
```

`.nova/` 项目级同构（backend/ + agents/ + frontend/tui/）。

**状态归属规则**（三问定案）：①影响模型/执行 → 后端；②随会话分支走 →
会话条目；③只是呈现 → 前端宿主目录。跨会话用户基线 → 各自域的 settings。

**关键语义**：

- 后端会话级状态 = session JSONL 条目（既有）；后端通用 KV 预留不建（无消费者）；
- 设置数量 = 权威域数量：后端一套，前端每宿主一套；
- trust 共享裁决、发现各管各（backend resolver / frontend discovery 各自扫描根）；
- `~/.agents/skills` 外部共享约定不动；
- 迁移：旧路径（ui-settings.json/ui-state/themes/旧资源目录）启动时检测即搬 + 日志。

**实施落点**：

- 后端：顶层自动发现扫描根在 `core/types/package/enums.py::TOP_LEVEL_RESOURCE_TYPE_DIRS`
  （散养四类 → `<base>/backend/<type>`，agents 平级不变；包内约定发现仍用
  `RESOURCE_TYPE_DIRS`），resolver 只改拼接点；迁移在
  `core/config/migration.py`（`AgentSessionServices.create` 单一挂接，
  user/project 两级 base 各迁——mv 语义、幂等、新位已有内容不合并不覆盖，
  记诊断日志）。
- 前端：路径族唯一出处 `src/paths.ts`（`frontend/tui/` 半区）；迁移在
  `src/migration.ts`（TUI 装配根 `app.ts` 构造期执行，先于键位/UISettings
  首读，消息经 transcript 透出）；散养资产扫描根在
  `src/resources/discovery.ts::discoverLooseAssets`（识别
  `tools/<tool>.ts` / `dialogs/<name>.ts` / `index.ts`；user 级永远可信，
  project 级未信任不 stat 不 import；注册顺序即覆盖优先级
  project > user > package > builtin，碰撞诊断复用既有机制）。

**配套任务（同批未做）**：`packages/nova_harness/{backend,frontend}` 伞目录归并
（现 nova_harness → backend、现 nova-tui → frontend；纯搬家变更集，独立做）。

## 10. 明确不做

- settings.json 后端文件 watcher（pi 也没有；外部手改需 /reload）；
- pi 的 reftable 监听/git watcher 重试（日常仓库碰不到）；
- 委派合作模式（持久子会话 + 消息收发）——留门不修路；
- manifest 作者默认关（`disabled_by_manifest` 预留态，无消费者）；
- footer 的 open/strict 牌子。

## 验证基线（本文所有 ✅ 项）

harness 1460 / bundle 397 / nova_agent 110 / nova-tui 353 / bundle 前端 178
全绿；PTY 主矩阵 45/45 + role-boundary/model-matrix/links/resume-switch 专项全过；
black/isort 干净；wire 契约再生成（方法表 75）。
