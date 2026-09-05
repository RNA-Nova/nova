# 环境变量

全部 `NOVA_*` 旋钮。provider 鉴权变量（`VOLCENGINE_API_KEY` 等）不在此表——见[模型与鉴权](../guide/models.md)。

## 路径与运行

| 变量 | 缺省 | 说明 |
|------|------|------|
| `NOVA_AGENT_DIR` | `~/.nova/agent` | 后端状态根（settings/sessions/packages/builtin 全在此下） |
| `NOVA_CONFIG_DIR` | `.nova` | 项目级配置目录名 |
| `NOVA_APP_NAME` | `nova` | 应用名（派生命名/再分发用） |
| `NOVA_PYTHON` | `python3` | pip/开发渠道的后端解释器；冻结形态下是 pip 宿主探测的首选 |
| `NOVA_BACKEND` | — | TUI 显式指定后端二进制路径（调试/非常规布局） |

## 行为开关

| 变量 | 说明 |
|------|------|
| `NOVA_OFFLINE=1` | 离线模式：包安装/更新检查/模型网络刷新全部跳过（模型调用本身除外） |
| `NOVA_SUBAGENT_MAX_CONCURRENCY` | 子代理全局并发上限（缺省 4） |
| `NOVA_TIMING=1` | 后端内部耗时观测（装配/加载分段计时打日志） |

## 安装器（install.sh）

| 变量 | 说明 |
|------|------|
| `NOVA_VERSION` | 钉版本（缺省查 latest release） |
| `NOVA_INSTALLER_RELEASES_BASE` | 发布源覆盖（支持 `file://` 本地演练） |
| `NOVA_INSTALL_DIR` / `NOVA_BIN_DIR` | 安装根 / bin 目录 |

## 构建管线（发布工程用，日常使用无关）

| 变量 | 说明 |
|------|------|
| `BUN` | `build-frontend.sh` 的 bun 路径 |
| `NOVA_BUILD_PYTHON` | `build-backend.sh` 建 venv 的解释器（缺省仓库 pixi dev） |
| `NPM_CONFIG_REGISTRY` | npm 源镜像（包管理 npm 源与自愈共用此约定） |
