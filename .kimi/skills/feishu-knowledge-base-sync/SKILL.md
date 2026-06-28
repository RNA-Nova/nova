---
name: feishu-knowledge-base-sync
description: "将本地 docs/ 目录文档批量同步到飞书知识库（Wiki）。当用户需要将文档同步到飞书、创建层级文档节点、批量更新飞书文档内容时使用。要求：本地有 docs/feishu.json 记录节点 token，且 docs/ 目录按分类组织（每个子目录有 index.md 入口）。"
---

# 飞书知识库文档同步

将本地 `docs/` 目录下的文档同步到飞书知识库，建立层级结构。

---

## 前置条件

1. `lark-cli` 已安装并完成认证（`lark-cli auth login`）
2. 本地存在 `docs/feishu.json`，包含根节点和分类入口的 token 映射：
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
               "001-xxx": { "node_token": "...", "obj_token": "..." }
           }
       }
   }
   ```
3. `docs/` 目录按规范组织（见下方目录结构）

---

## 目录结构规范

```
docs/
├── index.md                    # 根入口（飞书首页）—— **XML 格式**
├── feishu.json                 # 飞书节点 token 映射（关键配置文件）
├── architecture-design.md      # 根级独立文档 —— Markdown
├── devlog.md                   # 根级独立文档 —— Markdown
├── adr/
│   ├── index.md                # 分类入口 —— **XML 格式**
│   ├── 001-xxx.md              # 具体文档 —— Markdown
│   └── ...
├── guides/
│   ├── index.md                # 分类入口 —— **XML 格式**
│   ├── quickstart.md           # 具体文档 —— Markdown
│   └── ...
└── ...
```

**规则**：
- 每个子目录必须有 `index.md` 作为分类入口
- **分类入口（index.md）使用 XML 格式**，以便插入飞书特有元素（文档卡片引用）
- **普通文档使用 Markdown 格式**，保持编辑友好
- 根目录下的 `.md` 直接挂在根节点下
- 子目录下的 `.md` 挂在对应分类入口下

---

## 格式说明

### 普通文档：Markdown

具体内容文档（如 `quickstart.md`、`001-xxx.md`）始终使用 Markdown：

```bash
lark-cli docs +update --api-version v2 \
  --doc <obj_token> \
  --command overwrite \
  --doc-format markdown \
  --content "$(cat docs/xxx.md)"
```

### 分类入口：XML

`index.md` 使用 XML 格式，核心目的是通过 `<cite>` 标签生成**可点击的文档卡片**。

**引用其他文档的写法**：
```xml
<cite type="doc" doc-id="OBJ_TOKEN"></cite>
```

飞书会自动解析为文档卡片，显示被引用文档的标题，点击即可跳转。

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

> 写入时 `--doc-format xml`，不要用 `markdown`。

---

## 同步流程

### 步骤 1：读取 feishu.json

```bash
cat docs/feishu.json
```

获取各节点的 `node_token`（创建子节点用）和 `obj_token`（写入内容用）。

### 步骤 2：创建分类入口（如需新建）

为每个子目录创建分类入口节点（以根节点为父）：

```bash
lark-cli wiki +node-create \
  --parent-node-token <根节点TOKEN> \
  --title "分类名称" \
  --format json
```

记录返回的 `node_token` 和 `obj_token`，回填到 `feishu.json`。

### 步骤 3：写入分类入口内容（XML）

```bash
lark-cli docs +update --api-version v2 \
  --doc <分类obj_token> \
  --command overwrite \
  --doc-format xml \
  --content "$(cat docs/<分类>/index.md)"
```

### 步骤 4：创建并写入子文档

创建子节点：

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

### 步骤 5：同步根级独立文档

根目录下除 `index.md` 外的 `.md` 文件，直接在根节点下创建并写入（Markdown 格式）。

---

## 更新已有文档

**分类入口（index.md）**：

```bash
lark-cli docs +update --api-version v2 \
  --doc <obj_token> \
  --command overwrite \
  --doc-format xml \
  --content "$(cat docs/<分类>/index.md)"
```

**普通文档**：

```bash
lark-cli docs +update --api-version v2 \
  --doc <obj_token> \
  --command overwrite \
  --doc-format markdown \
  --content "$(cat <文件路径>)"
```

不需要重新创建节点，只需要更新内容。

---

## 注意事项

- 所有 `docs` 命令必须携带 `--api-version v2`
- `wiki +node-create` 创建的是知识库节点，返回 `node_token`（节点 ID）和 `obj_token`（文档对象 ID）
- 写入内容时用 `obj_token`，创建子节点时用 `node_token`
- 内容较长时建议用 Python 脚本调用 `lark-cli` 避免 shell 转义问题
- 命令输出不要重定向到 `docs/` 目录，防止生成垃圾文件
- 若新增子文档，创建后需将其 `node_token` 和 `obj_token` 回填到 `feishu.json`
