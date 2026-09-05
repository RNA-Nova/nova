# 安装与升级

Nova 的发布形态是**静态双二进制**：`nova`（TUI 前端）+ `runtime/nova-server`（后端），零系统依赖——不需要预装 Node.js 或 Python。

## 支持平台（安装矩阵）

| 平台 | 归档 | 安装方式 |
|------|------|---------|
| macOS arm64（Apple Silicon） | `nova-darwin-arm64.tar.gz` | 脚本 / 手动 |
| macOS x64（Intel） | `nova-darwin-x64.tar.gz` | 脚本 / 手动 |
| Linux x64 | `nova-linux-x64.tar.gz` | 脚本 / 手动 |
| Linux arm64 | `nova-linux-arm64.tar.gz` | 脚本 / 手动 |
| Windows x64 / arm64 | `nova-windows-*.zip` | 手动（见下文） |

## 方式一：curl 管道安装（推荐，macOS / Linux）

```bash
curl -fsSL https://github.com/RNA-Nova/nova/releases/latest/download/install.sh | sh
```

安装器做的事：

1. 预检平台与工具（`curl` / `tar` / `sha256sum` 或 `shasum`）；
2. 解析最新发布版本（或 `NOVA_VERSION=0.1.0` 钉版）；
3. 下载对应平台归档 + `SHA256SUMS` 并**校验 sha256**；
4. 解压到 `~/.nova/agent/install/releases/<版本>/`，翻转 `current` 符号链接；
5. 链接 `~/.local/bin/nova` → 当前版本；
6. 装后自检（`nova --version` 报号与目标版本不一致即判负）；
7. `~/.local/bin` 不在 PATH 时，交互式询问写入 shell profile（非终端环境改为打印指引）。

安装到自定义位置：`NOVA_INSTALL_DIR` / `NOVA_BIN_DIR` 覆盖缺省。

> macOS 说明：经 curl 下载的文件不经 LaunchServices，不携带 quarantine 属性，首跑不需要"系统设置 → 安全性"放行。**浏览器手动下载的归档则可能带 quarantine**——见下节。

## 方式二：手动下载归档

到 [Releases](https://github.com/RNA-Nova/nova/releases) 下载对应平台归档，解压即用：

```bash
tar -xzf nova-darwin-arm64.tar.gz -C nova-v0.1.0
cd nova-v0.1.0
./nova --version
```

校验下载（强烈建议）：

```bash
shasum -a 256 -c SHA256SUMS --ignore-missing   # macOS
sha256sum -c SHA256SUMS --ignore-missing       # Linux
```

macOS 浏览器下载的归档带 quarantine，首次运行前解除：

```bash
xattr -d com.apple.quarantine nova runtime/nova-server
```

**Windows**：下载 `nova-windows-x64.zip`，解压后运行 `nova.exe`（`runtime/nova-server.exe` 须与 `nova.exe` 保持在同一目录结构内）。

### 归档内容

```
nova                  # TUI 前端（bun 编译，单文件）
runtime/
  nova-server         # 后端（PyInstaller 冻结，RPC over stdio）
  _internal/          # 后端依赖（含内建 nova-base bundle）
package.json          # 版本元数据
CHANGELOG.md  LICENSE
export/               # /export HTML 导出模板
native/               # 终端输入原生助手（macOS/Windows）
```

`nova` 与 `runtime/` 必须保持相对位置——前端的运行后发现链是"二进制同目录的 `runtime/nova-server`"。

## 方式三：pip + npm（开发者渠道）

需要 Python ≥3.12 与 Node ≥22.19：

```bash
pip install nova-harness        # 后端（连带 nova_ai / nova_agent）
npm install -g nova-tui         # 前端（bin 名 nova）
```

此渠道下前端按以下链发现后端：`NOVA_BACKEND` 环境变量 → 二进制旁 `runtime/`（不适用）→ `NOVA_PYTHON` 指定解释器 → `python3 -m nova_harness.modes.rpc.cli`（系统 Python 里能找到 nova-harness 即可）。

> PyPI / npm 发布在 v0.1.0 尚未开启（workflow 已备，首发演练后打开）——当前可用渠道是脚本与手动归档。

## 源码安装（参与开发）

```bash
git clone https://github.com/RNA-Nova/nova.git
cd nova
pixi install -e dev            # Python 侧统一环境
cd packages/nova-tui && npm install && npm run build && npm link
```

## 升级

- **脚本安装**：重跑安装命令即可——新版本落 `releases/<新版本>/` 并翻转链接，旧版本目录保留（可手动清理 `~/.nova/agent/install/releases/`）。
- **手动归档**：下载新版归档解压，替换旧目录。
- 用户数据（settings / sessions / 已装包）在 `~/.nova/agent/`，与程序本体分离，升级不触碰。

## 卸载

```bash
curl -fsSL https://github.com/RNA-Nova/nova/releases/latest/download/install.sh | sh -s -- uninstall
```

摘除 `~/.local/bin/nova` 链接并删除 `~/.nova/agent/install/`。**用户数据保留**；彻底清除再加 `rm -rf ~/.nova/agent`。

## 验证安装

```bash
nova --version            # 前端版本（如 0.1.0）
# 二进制形态下后端随行至 runtime/：
"$(dirname "$(readlink -f "$(command -v nova)")")/runtime/nova-server" --version
```

首次启动 `nova` 会引导配置模型（provider + 鉴权），见[快速上手](quickstart.md)。
