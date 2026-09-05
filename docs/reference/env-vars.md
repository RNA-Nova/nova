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
| `NOVA_HTTP_IDLE_TIMEOUT_MS` | 模型请求的空闲超时（毫秒；缺省 300000 即 5 分钟，`0`/`disabled` 关闭——长 thinking 或慢网络场景用） |
| `NOVA_TELEMETRY=1` | 安装遥测 opt-in（当前仅写本地日志，无远程上报；缺省关，settings `enable_install_telemetry` 同义） |
| `NOVA_TIMING=1` | 后端内部耗时观测（装配/加载分段计时打日志） |

## 安装器（install.sh）

| 变量 | 说明 |
|------|------|
| `NOVA_VERSION` | 钉版本（缺省查 latest release） |
| `NOVA_INSTALLER_RELEASES_BASE` | 发布源覆盖（支持 `file://` 本地演练） |
| `NOVA_RELEASES_API_BASE` | latest 版本查询的 API 端点覆盖（镜像/内网演练用） |
| `NOVA_INSTALL_DIR` / `NOVA_BIN_DIR` | 安装根 / bin 目录 |

## 源码安装器（install-source.sh）

| 变量 | 说明 |
|------|------|
| `NOVA_SOURCE_DIR` | 直接用现有源码树（不 clone；仓库内运行时的缺省行为） |
| `NOVA_SOURCE_REF` | clone 的 ref（缺省解析最新 release tag） |
| `NOVA_REPO_URL` | clone 源仓库 URL 覆盖（fork/镜像用） |
| `NOVA_INSTALL_DIR` / `NOVA_BIN_DIR` | 与 install.sh 同义 |

## 构建管线（发布工程用，日常使用无关）

| 变量 | 说明 |
|------|------|
| `BUN` | `build-frontend.sh` 的 bun 路径 |
| `NOVA_BUILD_PYTHON` | `build-backend.sh` 建 venv 的解释器（缺省仓库 pixi dev） |
| `NPM_CONFIG_REGISTRY` | npm 源镜像（包管理 npm 源与自愈共用此约定） |
