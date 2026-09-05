# 包管理

Nova 的能力（工具、扩展、agent 组合、人格、模板、用户工具）全部由**包**分发。包管理器负责安装/卸载/更新/解析，TUI 的 `/packages` 面板提供交互界面。

## 命令

```bash
# pip 渠道用 nova-pkg；二进制形态用 runtime/nova-server pkg（下同）
nova-pkg list                  # 已安装包与其资源清单
nova-pkg install <源>          # 安装（见下"三种来源"）
nova-pkg uninstall <名或源>    # 卸载
nova-pkg update <名或源>       # 更新
nova-pkg info <名>             # 详情
nova-pkg validate <路径>       # 校验一个包目录是否合法
nova-pkg init                  # 按当前目录结构生成 [tool.nova] 段脚手架
```

TUI 内用 `/packages` 面板做同样的事（含更新提醒）。

## 三种来源

| 源 | 形态 | 更新语义 |
|----|------|---------|
| `path:/本地/目录`（或裸路径） | 本地目录；`--editable` 原地引用不复制 | 无远端概念，不参与更新检查 |
| `git:github.com/user/repo[@ref]` | clone 到 `<agent_dir>/packages/git/...`（活 clone） | `git ls-remote` 比对远端 HEAD；pin 完整 SHA 即冻结 |
| `npm:包名[@版本]` | npm registry 下载（`NPM_CONFIG_REGISTRY` 可换镜像/私服） | `dist-tags.latest` 比对；精确 pin 不查更新 |

git ref 支持分支/tag/短 SHA；npm 版本支持精确版、`^`/`~` range、x-range、比较器集、`||` 并集、hyphen range、dist-tag（省略 = latest）。

## 安装作用域

- **user 级**（默认）：`~/.nova/agent/settings.json` 登记，所有项目可用；
- **project 级**（`--local`）：`<cwd>/.nova/settings.json` 登记，仅本项目；同一 identity 两端都装时 project 优先。

settings 是包选择层的唯一事实源；安装事实以包旁的 `<name>.dist-info/` 快照为权威（防副本篡改漂移）。

## 规则

- **包间依赖**：包可声明 `requires = ["nova-base"]`——安装时被依赖包不在即拒绝（附安装提示）；卸载被依赖包时若依赖方还在即拒绝；
- **基础包守护**：`nova-base`（会话基础设施）任何形态下不可卸载；
- **信任**：装/卸是主动行为，不做 trust 检查；但 project 级包的**资源加载**受 project trust 门控（未信任的项目目录资源不会被读）；
- **离线**：`NOVA_OFFLINE=1` 时跳过一切网络动作（下载/更新检查），仅警告。

## 依赖装配

- 包带 Python 依赖（`pyproject.toml`）：pip 渠道装进当前环境；**二进制形态装进 `~/.nova/agent/packages/.site/`**（经本机 `python3 + pip` 宿主，运行时挂进冻结解释器的 `sys.path`）；
- 包带前端依赖（`frontend/package.json`）：安装期在包目录跑 `npm ci/install`；若当时失败（离线/无 npm），TUI 加载时发现缺失会**后台自愈补装**，完成后渲染器自动上线；
- 二进制加速件（`rg`/`fd` 等）：按包声明的 `binary_dependencies`（PyPI wheel）/ `binary_managed_dependencies`（框架注册表托管）自动安装；缺失不影响可用性（工具按 fd → rg → 纯 Python 三级链降级）。

## 排错

- 装了没生效：先 `/reload`；包面板看诊断（碰撞/加载失败都有记录）；
- `nova-pkg validate <路径>` 在**安装前**检查包目录合法性；
- 包日志与诊断：TUI `/debug` 导出打包。
