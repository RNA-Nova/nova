# Nova 构建、测试与开发流程

> 环境、命令、测试与依赖管理的操作手册。仓库结构见 docs/architecture.md，工作规则见根 AGENTS.md。

## 构建与开发命令

> 仓库已改用 **pixi** 作为统一的环境管理工具。根目录 `pyproject.toml` 中定义了 workspace，子包通过 editable path 依赖一次性安装。

### 环境初始化（pixi）

```bash
# 安装 pixi（如尚未安装）
curl -fsSL https://pixi.sh/install.sh | bash

# 安装默认环境（仅运行时依赖）
pixi install

# 安装开发环境（包含 black / isort / pytest / pytest-asyncio 等）
pixi install --environment dev
```

### 常用 pixi 任务

```bash
# 运行测试（在每个子包目录下独立执行，避免 tests 包名冲突）
pixi run -e dev test-ai
pixi run -e dev test-agent
pixi run -e dev test-harness
pixi run -e dev test-coding
pixi run -e dev test-all

# 格式化全部 Python 源码
pixi run -e dev format

# 直接调用已安装 CLI
pixi run -e dev nova-pkg list
```

### 手动在子包内运行测试

```bash
cd packages/<子包名>
pixi run -e dev pytest tests -m "not integration"
```

### 格式化

```bash
pixi run -e dev black packages/<子包名>/src/
pixi run -e dev isort packages/<子包名>/src/
```

对于 `nova_coding_agent`，Python 代码全部位于 `backend/` 半区，整体格式化：
```bash
pixi run -e dev black bundles/nova_coding_agent/backend
pixi run -e dev isort bundles/nova_coding_agent/backend
```

### 构建与发布

```bash
cd packages/<子包名>
pixi run -e dev python -m build      # 生成 wheel / sdist
# poetry publish    # 如需发布到 PyPI（仍保留 poetry 配置）
```

### Poetry 兼容说明

各子包仍保留 `pyproject.toml` 中的 Poetry 配置，可作为 pixi 不可用时的回退：

```bash
cd packages/<子包名>
poetry install
poetry run pytest tests -m "not integration"
```

### 可执行脚本（由 `nova_harness` 与 `nova_server` 注册）

安装后环境中会新增以下命令：

```bash
nova-harness              # 入口占位（当前仅 --version / help；headless 运行归 nova-server exec）
nova-server               # 启动 JSON-RPC 服务器（stdio/WS）
nova-pkg list             # 列出已安装的包/定义/工具
nova-pkg install <path>      # 支持 path:/git:/npm: 三种源（npm 源支持精确版本、^/~ range、x-range/通配段、比较器集、|| 并集与 hyphen range，省略 = latest）
nova-pkg uninstall <name>
nova-pkg update <name>
nova-pkg info <name>
nova-pkg validate <path>
nova-pkg init             # 根据当前目录结构生成 [tool.nova] 段
```

### `nova_client` 专属命令

```bash
cd packages/nova_client
npm install
npm run build      # TypeScript 编译到 dist/
npm test           # tsx --test（呈现映射单测）
npm run tui        # tsx 直接运行 TUI
npm start          # node 运行编译产物
npm link           # 全局注册 `nova` 命令
```

---


---

## 测试说明

- 所有包含 `pyproject.toml` 的子包均已将 `pytest` 声明为开发依赖。
- Python 测试目录结构：
  - `packages/nova_ai/tests/`
  - `packages/nova_agent/tests/`
  - `packages/nova_harness/tests/`
  - `packages/nova_server/tests/`
  - `bundles/nova_coding_agent/backend/tests/`
- **TS 测试（node:test + tsx）**：统一收在包根 `tests/`（**镜像 src 子路径**——`tests/modes/tui/controllers/keymap.test.ts` ↔ `src/modes/tui/controllers/keymap.ts`）——
  - `packages/nova_client/`：`npm test`（`tsx --test "tests/**/*.test.ts"`）
  - `bundles/nova_coding_agent/`：Python 侧 pytest + TS 侧 `npm test`（`tsx --test "tests/**/*.test.ts"`，渲染器与其算法测试，如 `tests/tools/edit.test.ts` 与 `tests/lib/edit-preview.test.ts`）；`npm run typecheck` 单独类型检查
- 真实 API 集成测试已用 `pytest.mark.integration` 标记；`nova_ai` 与 `nova_harness` 的集成测试需要 `VOLCENGINE_API_KEY` 等环境变量。
- 已通过 pixi 安装 dev 环境并验证：`nova_ai` 531 个、`nova_agent` 125 个、`nova_harness` 1334 个、`nova_server` 256 个、`nova_coding_agent` 528 个非集成测试全部通过；修改关键逻辑后应在对应子包内运行测试并确认结果。

运行方式：

```bash
# 使用 pixi（推荐）
pixi run -e dev test-ai
pixi run -e dev test-agent
pixi run -e dev test-harness
pixi run -e dev test-coding

# 手动在子包内运行
cd packages/<子包名>
pixi run -e dev pytest tests
pixi run -e dev pytest tests -m "not integration"    # 跳过真实 API 调用
pixi run -e dev pytest tests --cov=<包名> --cov-report=html

# Poetry 兼容方式
cd packages/<子包名>
poetry run pytest tests -m "not integration"
```

---


---

## 环境与依赖管理

- **环境管理**：仓库使用根目录 `pyproject.toml` 中的 `[tool.pixi.*]` 作为统一 workspace。新增或调整依赖时，优先在根 `pyproject.toml` 中声明，以便所有子包共享同一环境。
- **依赖新增**：
  - 若新增**第三方库**，优先在根 `pyproject.toml` 的 `[tool.pixi.pypi-dependencies]`（运行时）或 `[tool.pixi.feature.dev.pypi-dependencies]`（开发时）中声明，然后执行 `pixi install -e dev`。
  - 各子包仍保留 Poetry 配置作为兼容；如使用 Poetry，需在对应子包 `pyproject.toml` 的 `[tool.poetry.dependencies]` 中声明并执行 `poetry lock`（如有 lock 文件）。
