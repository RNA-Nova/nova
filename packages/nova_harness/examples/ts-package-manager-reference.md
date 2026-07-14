# TypeScript `coding-agent` 包管理系统完整参考

> 本文档基于 `/root/pi/packages/coding-agent/src/core/package-manager.ts` 整理，供 Nova Python 侧对齐参考。

---

## 1. 总体定位

TS 侧的包管理由单一的 `PackageManager` 类（`src/core/package-manager.ts`）负责，它同时承担：

- 包的安装、卸载、更新
- 资源发现与解析（`resolve()`）
- settings 中 package 条目的持久化

核心设计哲学：**包目录即安装目录**。npm/git 包装到管理目录后，整个目录保留；local 包原地使用。资源加载时直接读安装目录的 `package.json`。

---

## 2. 目录结构

### 2.1 用户级（global / user）

```
~/.pi/agent/                 # agentDir
├── settings.json            # 全局 settings
├── extensions/              # 自动发现：用户扩展
├── skills/                  # 自动发现：用户 skill
├── prompts/                 # 自动发现：用户 prompt template
├── themes/                  # 自动发现：用户 theme
├── npm/                     # npm 包安装根目录
│   ├── package.json         # npm 用来管理依赖的占位项目
│   └── node_modules/
│       ├── @some-scope/some-pkg/
│       │   ├── package.json # 包含 "pi" 段
│       │   └── extensions/  # 扩展目录
│       └── another-pkg/
└── git/                     # git 包安装根目录
    └── github.com/
        └── user/
            └── repo/        # 完整仓库克隆
                ├── package.json
                ├── extensions/
                └── skills/
```

### 2.2 项目级（project）

```
<cwd>/
└── .pi/                     # CONFIG_DIR_NAME
    ├── settings.json        # 项目级 settings
    ├── extensions/
    ├── skills/
    ├── prompts/
    ├── themes/
    ├── npm/
    │   └── node_modules/
    │       └── some-pkg/
    └── git/
        └── github.com/
            └── user/
                └── repo/
```

### 2.3 临时目录

```
~/.pi/agent/tmp/extensions/  # 临时扩展
~/.pi/agent/tmp/npm/         # 临时 npm
~/.pi/agent/tmp/git-<host>-<path>/  # 临时 git
```

---

## 3. Settings 文件

### 3.1 位置

- 全局：`~/.pi/agent/settings.json`
- 项目级：`<cwd>/.pi/settings.json`

### 3.2 与包相关的字段

```ts
interface Settings {
  // 包源列表：npm / git / local
  packages?: PackageSource[];

  // 本地资源路径（文件或目录），与 packages 并行
  extensions?: string[];
  skills?: string[];
  prompts?: string[];
  themes?: string[];

  // npm 命令，可配置为 bun / pnpm 等
  npmCommand?: string[];
}
```

其中 `PackageSource` 定义为：

```ts
export type PackageSource =
  | string                       // npm:foo、git:host/repo、local:./path
  | {
      source: string;            // 同上
      extensions?: string[];     // 过滤器：只加载匹配的扩展
      skills?: string[];
      prompts?: string[];
      themes?: string[];
    };
```

### 3.3 完整示例

**全局 settings.json：**

```json
{
  "packages": [
    "npm:@earendil-works/pi-extensions",
    "git:github.com/liujinming/pi-coding-tools@v1.2.0",
    {
      "source": "local:~/my-tools",
      "extensions": ["session-commands"],
      "skills": ["python"]
    }
  ],
  "extensions": [
    "~/.pi/agent/extensions/my-local-ext"
  ],
  "skills": [
    "~/.pi/agent/skills/my-local-skill"
  ],
  "npmCommand": ["npm"]
}
```

**项目级 settings.json：**

```json
{
  "packages": [
    {
      "source": "local:./tools",
      "extensions": ["project-ext"]
    }
  ],
  "extensions": [
    "!./pi/extensions/legacy-ext"
  ]
}
```

---

## 4. 包 Manifest

TS 侧每个包根目录放一个 `package.json`，Nova 相关的资源声明在 `"pi"` 段里。

### 4.1 最小示例

```json
{
  "name": "pi-coding-tools",
  "version": "1.2.0",
  "description": "Coding tools for pi agent",
  "pi": {
    "extensions": ["./extensions/session-commands"],
    "skills": ["./skills/python-best-practices"],
    "prompts": ["./prompts/code-review"],
    "themes": ["./themes/dark"]
  }
}
```

### 4.2 字段说明

| 字段 | 说明 |
|---|---|
| `name` / `version` / `description` | 标准 npm 字段，也用于展示 |
| `pi.extensions` | 扩展目录或文件路径数组 |
| `pi.skills` | skill 目录或文件路径数组 |
| `pi.prompts` | prompt template 路径数组 |
| `pi.themes` | theme 文件路径数组 |

### 4.3 路径规则

- 相对路径：相对于 `package.json` 所在目录
- 也支持 glob 或 override 模式，例如 `"!./extensions/experimental"`

---

## 5. Source 类型与安装行为

### 5.1 npm

**格式：**

```
npm:package-name
npm:package-name@^1.0.0
npm:@scope/package@1.2.3
```

**安装行为：**

1. 解析为 `NpmSource { type: "npm", name, version?, range?, pinned }`
2. 计算安装路径：
   - user: `~/.pi/agent/npm/node_modules/<name>/`
   - project: `<cwd>/.pi/npm/node_modules/<name>/`
   - temporary: `~/.pi/agent/tmp/npm/node_modules/<name>/`
3. 在该目录下确保有一个占位 `package.json`
4. 执行 `npm install <spec> --prefix <installRoot> --legacy-peer-deps`
5. 安装后的完整包目录保留，`package.json` 可读

**更新行为：**

- 如果版本不匹配或目录不存在，重新 `npm install`
- 支持 pinned / range / latest 策略

### 5.2 git

**格式：**

```
git:github.com/user/repo
git:github.com/user/repo@v1.0.0
https://github.com/user/repo
https://github.com/user/repo@branch-name
```

**安装行为：**

1. 解析为 `GitSource { type: "git", host, path, repo, ref? }`
2. 计算安装路径：
   - user: `~/.pi/agent/git/<host>/<path>/`
   - project: `<cwd>/.pi/git/<host>/<path>/`
   - temporary: `~/.pi/agent/tmp/git-<host>-<path>/`
3. `git clone <repo> <targetDir>`
4. 如果有 `ref`，`git checkout <ref>`
5. 如果存在 `package.json`，执行 `npm install` 安装其依赖
6. 保留完整仓库目录

**更新行为：**

- 无 ref：fetch 默认分支并 `reset --hard` 到最新，`git clean -fdx`
- 有 ref：fetch 指定 ref 并 checkout
- 重新安装 npm 依赖

### 5.3 local

**格式：**

```
local:/absolute/path
local:./relative/path
/absolute/path
./relative/path
```

**安装行为：**

1. 解析为 `LocalSource { type: "local", path }`
2. `install()` 方法**不复制任何文件**，仅校验路径存在
3. 通过 `installAndPersist()` 把 source 加到 settings
4. resolve 时直接读取该本地路径的 `package.json`

**特点：**

- local 包不会被复制到 `~/.pi/agent/`
- 资源加载时直接引用原始路径
- 如果原始路径被删除，资源失效

---

## 6. install / installAndPersist / remove / update

### 6.1 install

```ts
await packageManager.install("npm:@earendil-works/pi-extensions");
await packageManager.install("git:github.com/user/repo@v1.0", { local: true });
await packageManager.install("local:./my-tools");
```

只安装，不写 settings。

### 6.2 installAndPersist

```ts
await packageManager.installAndPersist("npm:foo", { local: true });
```

等价于：

```ts
await packageManager.install(source, options);
packageManager.addSourceToSettings(source, options);
```

这是 `pi install` 命令实际调用的方法。

### 6.3 remove / removeAndPersist

```ts
await packageManager.remove("npm:foo");           // 只删安装目录
await packageManager.removeAndPersist("npm:foo"); // 同时从 settings 移除
```

### 6.4 update

```ts
await packageManager.update();          // 更新所有 settings.packages
await packageManager.update("npm:foo"); // 只更新匹配的包
```

逻辑：

1. 遍历 project + global settings 的 packages
2. 按包 identity 匹配（npm 用 name，git 用 host/path）
3. 对 npm 包：检查版本，必要时重新 install
4. 对 git 包：fetch + reset

---

## 7. 资源解析（resolve）

### 7.1 完整流程

```ts
const resolved = await packageManager.resolve(onMissing?);
// resolved.extensions / skills / prompts / themes
```

内部步骤：

1. **解析 settings.packages**
   - project 优先于 global
   - 去重：同 identity 时 project 覆盖 global
   - 对每个 package source，拿到 `installedPath`
   - 调用 `collectPackageResources(installedPath, ...)` 读 `package.json` 的 `pi` 段

2. **自动发现**
   - project 级：`<cwd>/.pi/{extensions,skills,prompts,themes}/`
   - user 级：`~/.pi/agent/{extensions,skills,prompts,themes}/`
   - `.agents/skills/`（ ancestors 路径）

3. **应用 overrides**
   - settings 里的 `extensions` / `skills` / `prompts` / `themes` 数组可作为启用/禁用开关

### 7.2 Package 资源的 PathMetadata

```ts
{
  source: "npm:@earendil-works/pi-extensions",  // 原始 source spec
  scope: "user" | "project" | "temporary",
  origin: "package",
  baseDir: "/home/user/.pi/agent/npm/node_modules/..."  // 安装目录
}
```

---

## 8. 自包含性

TS 侧天然自包含：

| 来源 | 安装后是否依赖原始 source | 原因 |
|---|---|---|
| npm | 否 | 包被装到 `~/.pi/agent/npm/`，完整保留 |
| git | 否 | 仓库被 clone 到 `~/.pi/agent/git/`，完整保留 |
| local | 是 | 不复制，直接引用原始路径 |

因此 npm/git 包安装后，原始 source 可以删除；local 包则不行。

---

## 9. 与 Nova Python 侧的关键差异

| 维度 | TS `coding-agent` | Python `nova_harness`（当前） |
|---|---|---|
| 核心文件 | `package-manager.ts` | `core/package/core.py` + `core/package/resolver/resolver.py` |
| manifest 文件 | `package.json` 的 `"pi"` 段 | `pyproject.toml` 的 `[tool.nova]` 段 |
| 安装模型 | 保留整个包目录 | bundle 拆散复制，只保留条目 |
| npm 支持 | ✅ 原生支持 | ❌ 未实现 |
| local 安装 | 原地引用 | 拆散复制 |
| install 后写 settings | ✅ `installAndPersist` | ❌ 需手动写 |
| 自包含性 | npm/git 天然自包含 | 安装后仍依赖 source 目录 |
| 资源类型 | extensions / skills / prompts / themes | agents / tools / skills / extensions |

---

## 10. 建议的对齐方向

若 Nova Python 要向 TS 对齐，可考虑：

1. **安装后自动持久化到 settings.packages**（`installAndPersist`）
2. **保留安装目录的 manifest**：把 `pyproject.toml` 复制到 `~/.nova/agent/packages/<name>/`
3. **支持 npm 包**：用 npm/pip 混合管理扩展包
4. **Resolver 优先读安装目录 manifest**，source 作为 fallback
