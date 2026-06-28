# 飞书知识库协作流

本文档描述 `nova_ai` 包的文档管理与飞书同步自动化流程。目标：**将本文档发给任意 AI 智能体，智能体即可复刻整套流程。**

> 注意：`docs/` 目录下的文件专为飞书同步协作而维护，不放入 GitHub。

---

## 一、流程概述

```
本地文档（Markdown + XML）
    ↓
本地目录结构（docs/）
    ↓
lark-cli 批量同步
    ↓
飞书知识库（Wiki）层级节点
    ↓
团队成员在线协作
```

**核心原则**：
1. **本地为源**：所有文档先在本地 `docs/` 维护，确保版本可控
2. **目录即层级**：本地文件夹结构直接映射为飞书知识库节点层级
3. **每个目录一个入口**：每个子目录都有 `index.md`，作为飞书上该分类的入口页面
4. **分类入口用 XML**：`index.md` 使用 XML 格式，支持飞书文档卡片引用
5. **普通文档用 Markdown**：具体内容文档保持 Markdown，编辑友好
6. **一键同步**：通过脚本批量创建节点 + 写入内容，不手动复制粘贴

---

## 二、目录结构设计

### 根目录

```
docs/
├── index.md                    # 根入口（飞书知识库首页）—— XML 格式
├── feishu.json                 # 飞书节点 token 映射（关键配置文件）
├── architecture-design.md      # 根级独立文档 —— Markdown
├── devlog.md                   # 根级独立文档 —— Markdown
├── adr/                        # 架构决策记录分类
├── guides/                     # 使用与维护指南分类
├── conventions/                # 代码约定分类
└── reference/                  # API 参考分类
```

### 每个子目录结构

```
adr/
├── index.md                    # 分类入口（飞书上显示为"架构决策记录"）—— XML
├── 001-protocol-vs-abc.md      # 具体文档 —— Markdown
├── 002-registry-no-split.md
└── ...

guides/
├── index.md                    # 分类入口（"使用与维护指南"）—— XML
├── quickstart.md               # 具体文档 —— Markdown
├── examples.md
└── ...
```

**命名规则**：
- 分类入口统一叫 `index.md`，飞书标题用中文（如"架构决策记录"）
- 具体文档用有意义的文件名，飞书标题可以中文或英文
- `feishu.json` 保存根节点、分类入口及子文档的 `node_token` 和 `obj_token`

---

## 三、关键配置文件 `feishu.json`

```json
{
    "index": {
        "url": "https://xxx.feishu.cn/wiki/TOKEN",
        "node_token": "...",
        "obj_token": "..."
    },
    "adr": {
        "node_token": "...",
        "obj_token": "...",
        "children": {
            "001-protocol-vs-abc": {
                "node_token": "...",
                "obj_token": "..."
            }
        }
    }
}
```

**作用**：
- 记录根节点和所有分类入口、子文档的 `node_token`（节点 ID）和 `obj_token`（文档对象 ID）
- 所有新文档都以根节点或其分类入口为父节点创建
- `node_token` 用于创建子节点，`obj_token` 用于写入文档内容

---

## 四、格式选择

### 普通文档：Markdown（`--doc-format markdown`）

所有具体内容文档（`architecture-design.md`、`quickstart.md`、`001-xxx.md` 等）使用 Markdown。

原因：
- 编辑友好，无需学习 XML
- 飞书对 Markdown 支持良好
- 内容零损耗同步

### 分类入口：XML（`--doc-format xml`）

`index.md`（根入口和各分类入口）使用 XML 格式。

原因：需要插入飞书特有元素——**文档卡片引用**（`<cite>` 标签）。

**引用其他文档的写法**：
```xml
<cite type="doc" doc-id="OBJ_TOKEN"></cite>
```

飞书会自动将其渲染为可点击的文档卡片，显示被引用文档的标题。

**完整示例**（guides/index.md）：
```xml
<title>使用与维护指南</title>

<p>面向二次开发者和维护者的操作手册。</p>

<h2>使用者指南</h2>

<table>
  <thead><tr><th>文档</th><th>适合场景</th></tr></thead>
  <tbody>
    <tr>
      <td><cite type="doc" doc-id="HmdZdWN6TonBUExry5TcAOfGnMe"></cite></td>
      <td>第一次使用，5 分钟发起请求</td>
    </tr>
  </tbody>
</table>
```

> `OBJ_TOKEN` 是被引用文档的 `obj_token`，可从 `feishu.json` 获取。

---

## 五、同步操作步骤

### 前置条件

1. 安装 `lark-cli` 并完成认证
2. 确保有知识库写权限
3. 确认 `feishu.json` 中的根节点 token 有效

### 步骤 1：读取 feishu.json

```bash
cat docs/feishu.json
```

获取各节点的 `node_token`（创建子节点用）和 `obj_token`（写入内容用）。

### 步骤 2：创建分类入口（根节点的子节点）

如需新建分类：

```bash
lark-cli wiki +node-create \
  --parent-node-token <根节点TOKEN> \
  --title "分类名称" \
  --format json
```

**返回值关键字段**：
- `node_token` —— 知识库节点 token，后续创建子文档时使用
- `obj_token` —— 文档对象 token，后续写入内容时使用

创建后将其 `node_token` 和 `obj_token` 回填到 `feishu.json`。

### 步骤 3：写入分类入口内容（XML）

```bash
lark-cli docs +update --api-version v2 \
  --doc <分类obj_token> \
  --command overwrite \
  --doc-format xml \
  --content "$(cat docs/<分类>/index.md)"
```

### 步骤 4：创建子文档

在分类入口下创建子节点：

```bash
lark-cli wiki +node-create \
  --parent-node-token <分类入口node_token> \
  --title "文档标题" \
  --format json
```

写入 Markdown 内容：

```bash
lark-cli docs +update --api-version v2 \
  --doc <子文档obj_token> \
  --command overwrite \
  --doc-format markdown \
  --content "$(cat docs/<分类>/<文件名>.md)"
```

创建后将其 `node_token` 和 `obj_token` 回填到 `feishu.json`。

### 批量同步脚本模板

```python
import subprocess
import json

def create_wiki_node(parent_token, title):
    """创建知识库节点，返回 (node_token, obj_token)"""
    result = subprocess.run(
        ['lark-cli', 'wiki', '+node-create',
         '--parent-node-token', parent_token,
         '--title', title, '--format', 'json'],
        capture_output=True, text=True
    )
    data = json.loads(result.stdout)
    return data['data']['node_token'], data['data']['obj_token']

def write_index_content(obj_token, file_path):
    """将分类入口 XML 写入飞书文档"""
    with open(file_path) as f:
        content = f.read()
    subprocess.run(
        ['lark-cli', 'docs', '+update', '--api-version', 'v2',
         '--doc', obj_token, '--command', 'overwrite',
         '--doc-format', 'xml', '--content', content],
        capture_output=True, text=True
    )

def write_doc_content(obj_token, file_path):
    """将普通 Markdown 文档写入飞书"""
    with open(file_path) as f:
        content = f.read()
    subprocess.run(
        ['lark-cli', 'docs', '+update', '--api-version', 'v2',
         '--doc', obj_token, '--command', 'overwrite',
         '--doc-format', 'markdown', '--content', content],
        capture_output=True, text=True
    )

# 使用示例
parent_node, parent_obj = create_wiki_node('<根TOKEN>', '架构决策记录')
write_index_content(parent_obj, 'docs/adr/index.md')

child_node, child_obj = create_wiki_node(parent_node, 'ADR-001')
write_doc_content(child_obj, 'docs/adr/001-protocol-vs-abc.md')
```

---

## 六、更新已有文档

**分类入口（index.md）更新**：

```bash
lark-cli docs +update --api-version v2 \
  --doc <obj_token> \
  --command overwrite \
  --doc-format xml \
  --content "$(cat docs/<分类>/index.md)"
```

**普通文档更新**：

```bash
lark-cli docs +update --api-version v2 \
  --doc <obj_token> \
  --command overwrite \
  --doc-format markdown \
  --content "$(cat <文件路径>)"
```

**不需要重新创建节点**，只需要更新内容。

---

## 七、新增文档流程

1. 在本地 `docs/` 对应目录下新建 `.md` 文件（普通文档用 Markdown）
2. 如果新建的是分类，同时创建 `index.md`（用 XML 格式）
3. 更新相关 `index.md` 中的导航引用（如有必要）
4. 执行同步脚本创建节点并写入内容
5. 将新节点的 `node_token` 和 `obj_token` 回填到 `feishu.json`

---

## 八、常见问题

### Q1：命令输出被误存为文件？

如果看到类似 `docs/参考.json` 的垃圾文件，这是命令输出重定向错误导致的，直接删除即可。正式的同步配置只保存在 `feishu.json`。

### Q2：内容写入失败？

检查：
1. `--api-version v2` 是否遗漏
2. `obj_token` 是否正确（不是 `node_token`）
3. `index.md` 是否用了 `--doc-format xml`，普通文档是否用了 `--doc-format markdown`
4. 内容中是否包含特殊字符导致 shell 转义问题（建议用 Python 脚本避免）

### Q3：如何找到已有文档的 obj_token？

```bash
lark-cli wiki nodes list --params '{"space_id":"<空间ID>","parent_node_token":"<父节点TOKEN>"}' --format json
# 返回值中的 obj_token 字段
```

或从 `feishu.json` 中直接读取。

### Q4：bot 身份 vs user 身份？

知识库操作优先使用 `--as user`，因为知识库是用户个人资源。只有用户明确要求 bot 视角时才用 `--as bot`。

---

## 九、给 AI 智能体的指令模板

当你把本文档发给 AI 智能体时，同时提供以下指令：

> 请根据 `docs/knowledge-base-workflow.md` 中的流程，帮我把 `docs/` 目录下的所有文档同步到飞书知识库。
>
> 步骤：
> 1. 读取 `docs/feishu.json` 获取各节点的 `node_token` 和 `obj_token`
> 2. 为每个子目录创建分类入口（以根节点为父）
> 3. 为每个具体文档创建子节点（以对应分类入口为父）
> 4. 将分类入口 `index.md` 以 **XML 格式**（`--doc-format xml`）写入
> 5. 将普通文档以 **Markdown 格式**（`--doc-format markdown`）写入
>
> 注意事项：
> - 每个子目录的入口文件名是 `index.md`，飞书标题用中文
> - `index.md` 中引用其他文档时使用 `<cite type="doc" doc-id="OBJ_TOKEN"></cite>`
> - 所有 `docs` 命令必须带 `--api-version v2`
> - 创建节点后将其 `node_token` 和 `obj_token` 回填到 `feishu.json`
