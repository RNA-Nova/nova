# Nova 语言与本地化建模标准

> 坐标：横切·语言与本地化（体系总图与状态三档约定见 `docs/README.md`）。
>
> 本文建模的是**系统内一切面向人阅读的文本的语言与住所**——代码注释、文档、commit、产品 UI 文案、诊断原文、模型可见文本。
> 类型的住所与表示选型见 `docs/crosscutting/data-modeling.md`；错误/警告/诊断的分类与通道见 `docs/crosscutting/diagnostics-modeling.md`——本文只回答那套文本**用什么语言写、住在哪里、由谁翻译成用户看到的语言**，不重复其分类学。
> 参照系：ZCode（中文注释成文 + 前端语言包双语 + 后端零文案）为第一参考；LSP/VS Code（`nls.localize` 客户端渲染）与 Kubernetes（`reason` 机器码 + 英文调试文）为行业佐证。
> 状态：**目标态定稿**。【现状】= 已建成；【待补】= 设计已定、排期未做——新增任何文本即日起按本文设计，不等落地。

**总纲**：三种读者，三种语言，各就其位——**机器读语义**（枚举与参数，后端的唯一产出）、**人读呈现**（前端语言包按 locale 渲染）、**模型读上下文**（persona/系统提示，语言策略独立于 UI 语言）。呈现语言归前端，开发语言归仓库，两层互不渗透。

---

## 一、铁律（六条）

1. **呈现语言归前端**：后端只出结构化语义（kind/code/参数），不出面向用户的成句文案——语言包、locale 偏好、文案渲染全部归客户端（前端域）。在后端写死展示文案，就是把语言焊死在服务端，再多客户端各自为政。
2. **语义锚点先行**：能被翻译的文本必须挂在语义键上（`errorKind`/`WarningKind`/功能域键）——**没有锚点不设键**。没有锚的文本，前端连翻译的把手都摸不到，只能对着原文做正则（pi 的文案匹配老路，本文不治自绝）。
3. **一种文本一个住所**：注释、文档、产品文案、诊断原文、模型可见文本各有唯一住所（见第二节总表）。同类文本两处安家，漂移即刻开始。
4. **缺键响亮，双包一致**：缺失的翻译渲染**键名本身**（显眼即发现，ZCode 同款）；zh/en 两包键集必须全等（门禁机械执法）。"先写中文、英文后补"这条挂账路径从制度上不存在。
5. **诊断原文与呈现文案分层**：`error_message` 等错误原文是**调试通道**（英文，给开发者与日志看）；用户看到的呈现文案由前端按 kind 渲染。两层不混用——诊断原文不负责好听，呈现文案不负责完整。
6. **模型可见文本语言独立**：persona/系统提示/工具描述是给**模型**的上下文，语言策略按模型行为评估，与 UI 语言解耦——UI 中文与模型上下文英文可以并存，反过来也成立。

## 二、文本分层总表（五层，各就一个家）

| 层 | 内容 | 语言 | 住所 |
|---|---|---|---|
| **机器语义** | `errorKind`/`WarningKind`/错误码/参数（provider、retryAfterMs） | 无语言（枚举值 camel） | `nova_protocol` 词汇 + 线上 schema |
| **产品呈现文案** | UI 装饰文本、按钮/菜单/对话框、错误与警告的用户面呈现 | zh-CN + en-US 双包 | `nova_client/src/i18n/locales/`（唯一住所） |
| **诊断原文** | `error_message`、异常消息、refresh errors、日志 | 英文（运维惯例） | 服务端各产出点（诊断/调试通道，非呈现） |
| **模型可见文本** | persona、系统提示、工具描述、prompt 模板 | 按模型策略（当前英文为主，人格文本可中文） | bundle `personas/`/`prompts/`（经 `[tool.nova]` 分发） |
| **开发面文本** | 代码注释、文档、AGENTS.md、commit message | 见第三节 | 仓库内 |

## 三、开发语言政策（仓库约定）

- **代码注释与 docstring**：**中文**。设计理由、修复依据、纪律注记全部中文；bug 修复注释必须写清原因与依据（ZCode `AGENTS.md:100` 同款约定）。
- **文档**：**中文正本**（`docs/`、包级 AGENTS.md、README）。英文副本**按需**——对外发布/开源时刻再立，不提前养双份。
- **commit message**：**英文**（行业惯例与工具链生态）。
- **标识符**：英文（类型/函数/变量/文件名），无例外。
- **技术词不硬翻**：kind、stream、abort、reducer 等保留英文原词，与中文正文混排是常态；术语的对表关系见第七节词汇表纪律。

## 四、产品语言体系

### 语言包模块

```
nova_client/src/i18n/
├── locales/
│   ├── zh-CN.ts          # Record<string, string>（中文包）
│   └── en-US.ts          # Record<string, string>（英文包）
├── index.ts              # formatMessage({ id }, values?) + locale 状态
└── （无框架依赖——模块级函数，TUI 直接消费，不引 React context）
```

### 键设计（两族并存）

1. **语义锚族**——错误/警告/诊断的用户面呈现，键 = 语义枚举线值：`error.rateLimited`、`error.loginRequired`、`warning.capability.managedUnavailable`……锚点即 `NovaErrorKind`/`WarningKind` 的线上值——**前端翻译的把手**，也是当初把 kind 焊上消息的先见在此兑现。
2. **功能域族**——UI 装饰文案，键 = 位置域点路径（ZCode 同款）：`transcript.toolCard.retry`、`dialog.permission.title`、`palette.placeholder.model`……

### 渲染与回退

- `formatMessage({ id }, values?)`：键查找 + `{placeholder}` 插值——后端只传参数（provider 名、计数），不产成句。
- **键类型化**：`id` 参数是从 zh-CN 包**构建期生成的联合类型**（`type MessageId = "error.rateLimited" | …`），随包重生、漂移测试保鲜——键拼错在**编译期**暴露（ZCode 的裸 `Record<string, string>` 只能等运行时渲染出键名才发现；这是类型管道文化给的上限）。
- **回退链**：当前 locale → `DEFAULT_LOCALE`（zh-CN）→ 渲染键名本身。缺键永远可见，永不静默空串。

### 数值与时间的本地化（不拼字符串）

文案查表解决不了语法结构差异，一律经 locale-aware 格式化函数：

- **相对时间/时长/日期时间**：`Intl.RelativeTimeFormat`/`Intl.DurationFormat`/`Intl.DateTimeFormat`（Node 自带 ICU）或薄封装——"3 分钟前"与 "3 minutes ago"、"20s"与"20 秒"由格式化器出，不手写；
- **数字与千分位**：`Intl.NumberFormat`（token 计数、价格）；
- **复数规避**：不做单复数拼接（英文 `1 file / 2 files` 与中文无复数的语法分歧）——句式一律"共 {n} 项"式参数化，`{n}` 经 `NumberFormat` 格式化后插值。

### locale 偏好

- `LocalePreference = "system" | "zh-CN" | "en-US"`；`system` 档跟随系统 locale；
- 住所：**UISettings**（前端域，`~/.nova/agent/frontend/tui/settings.json`——与主题/键位同层，不进后端 settings）；
- TUI 内切换命令（如 `/language`）经 UISettings 持久化，立即生效。

### 布局与宿主面

- **布局纪律**（ZCode `DESIGN.md:260` 同款）：文案长度按双语言最差情形设计——footer/对话框/选择器行宽必须同时容忍中文全角与英文长词，不为"短英文恰好放下"硬编码布局。
- **UI 宿主**（TUI/将来 Web）：语言包 + locale 偏好；
- **headless/print 面**（无 UI 宿主）：诊断原文（英文）——语言包只服务有 UI 的宿主；
- **导出/分享**（HTML 导出、gist）：前端渲染面，走同一语言包——导出即呈现。

## 五、诊断原文的角色

服务端错误消息的现状是中英混杂（`No API key for provider...` 英文 / `订阅源拉取失败` 中文），同一通道两种语言且都焊在后端。目标形态：

- 服务端产出的人类可读错误消息**统一英文**（诊断原文 = 调试通道）；
- 用户面的呈现由前端按 `errorKind`/`WarningKind` 键渲染（可中文）；
- **新增诊断/错误消息只写英文**——用户面需要中文呈现的场景，同时在前端语言包加键；
- 存量中文诊断消息随语言包上线逐批收编（中文文案搬进 zh-CN 包，服务端留英文原文）。典型收编例：auth 交互引导（`AuthEvent` 只应携带 user_code/URL 等语义，引导文案归前端键渲染）。

## 六、测试与门禁

- **键集一致性门禁**：zh-CN 与 en-US 键集全等，diff 为空（新增键忘翻另一包即红）；
- **插值参数一致**：同一键在两包中的 `{placeholder}` 集合一致；
- **键类型生成物漂移测试**：zh-CN 包变更 → 联合类型重新导出，漂移测试保鲜（schema 导出同款机制；类型化落地后，"键存在性"由编译器执法，无需运行时扫描）；
- **i18n 文件豁免**：locale 文件不受单文件行数上限约束（ZCode 同款豁免——语言包注定大）。

## 七、术语一致（领域词汇表）

术语混乱是隐形的语言问题（"归一/归一化/归一枢纽"已在仓库里混用）。每个领域立 **CONTEXT 式词汇表**（ZCode 同款：术语英文原词 + 中文定义 + `_Avoid_` 反义词表）——文档与产品文案的用术语以词汇表为唯一对表。住所：各领域文档的"词汇"节；新增术语先进表再使用。

本篇自用词汇即按此纪律：

| 术语 | 含义 | 避免混用 |
|---|---|---|
| 语言包（language pack） | `nova_client/src/i18n/locales/` 的双语 Record 映射 | 翻译文件、文案表（口语可用，文档统一"语言包"） |
| 语义锚点 | 翻译键所挂载的语义枚举值（errorKind/WarningKind） | 错误码（它是线值不是 HTTP 码） |
| 语义锚族 / 功能域族 | 两类键设计：按语义枚举 / 按 UI 位置域 | 混为一谈 |
| 诊断原文 | 服务端英文错误消息（调试通道） | 用户文案、错误提示 |
| 呈现文案 | 前端按 locale 渲染的用户面文本 | 翻译（它只是产物之一） |

## 八、现状与待补清单

| 项 | 状态 |
|---|---|
| 注释/文档中文惯例（根 AGENTS.md 成文） | 【现状】 |
| 语义锚点（`errorKind`/`WarningKind` 15+4 变体，camel 线上键） | 【现状】 |
| 哑管道语义上线（线上传事实不传呈现——`rpc_capabilities.md` 原则） | 【现状】 |
| 模型可见文本与 UI 语言解耦（persona 经 bundle 分发） | 【现状】 |
| commit 语言约定（英文）成文 | 【现状】（本文首立） |
| 诊断原文统一英文 + 存量中文收编 | 【待补】（随语言包上线逐批） |
| `nova_client/src/i18n/` 语言包模块（双包 + formatMessage + locale 偏好） | 【待补】 |
| 键集一致性/插值一致性/键类型生成漂移 三门禁 | 【待补】 |
| `/language` 切换命令 + UISettings 持久化 | 【待补】 |
| 用户/项目级语言包覆盖（themes 三源同款） | 【目标态】（v1 只 builtin，有真实需求再立） |
| 文档英文副本（对外发布时刻） | 【目标态】（需要时再立，不提前养双份） |

## 九、明确不做

- **不引 i18n 框架**（react-i18next/fluent/gettext）——`Record<Locale, Record<string, string>>` + 一个 `formatMessage` 函数即全部机制（ZCode 实证够用）；
- **不做后端文案翻译通道**——语言永不上线、不进后端 settings；
- **不给注释/文档立机器翻译门禁**——开发面语言是人约定，不需要 CI 执法；
- **不翻日志与遥测**——开发者/机器通道，英文恒定；
- **模型可见文本不随 UI locale 联动**——语言策略按模型行为独立评估（铁律 6）。
