# 分发与发布

## 三种分发渠道（用户视角三源）

| 渠道 | 安装命令 | 适用 |
|------|---------|------|
| 本地路径 | `nova-pkg install path:/path/to/bundle` | 开发期/私有分发（目录共享、U盘、内网） |
| git 仓库 | `nova-pkg install git:github.com/you/your-bundle` | 开源分发（整仓 clone；`@v1.0` 钉版） |
| npm registry | `nova-pkg install npm:your-bundle` | 公开发布（版本语义最全：range/dist-tag） |

> git 源是**整仓 clone**——monorepo 子目录形态（一个仓库多个 bundle）需放包根能被识别；多 bundle 仓库建议每 bundle 一个 git 仓或走 npm。

## 版本与更新语义

- **npm**：`npm:pkg@1.2.3`（pin 不查更新）/ `@^1.2` / `@beta`（dist-tag）/ 省略 = latest；
- **git**：`@main` 跟分支 / `@v1.0.0` 跟 tag / `@<40位SHA>` 钉死冻结（不查更新）；
- **path**：无更新概念（本地目录即事实源）。

发布节奏建议：npm 源走 semver，tag 发布与 changelog 同步；git 源至少打 tag（用户好 pin）。

> 官方编程能力即此渠道：`npm:nova-coding-agent`（A 型整包——pyproject + backend + agents + frontend 一起入包，嵌套 `frontend/node_modules` 由 `.npmignore` 守住不进包）。官方包的发布挂在 release workflow 的 `publish-npm-bundle` 段。

## 打包前检查单

```bash
nova-pkg validate ./my-bundle     # 结构 + manifest 合法性
```

- [ ] `[tool.nova]` 七类资源路径都存在且形态正确（单文件/目录纪律）；
- [ ] 工具元数据三件套（name/description/parameters）齐全，`description` 写清使用边界；
- [ ] `requires` 声明完整（用了 `nova_base.ui_primitives` 就 `requires = ["nova-base"]`）；
- [ ] Python 依赖都在 `[tool.poetry.dependencies]` 声明（`auto_install_dependencies = true` 才会自动装）；
- [ ] 前端依赖在 `frontend/package.json` 的 `dependencies`（运行时）——`devDependencies` 只放测试/类型；
- [ ] 二进制依赖按三族正确归类（wheel 可装 / 框架托管 / 系统校验）；
- [ ] 测试齐备：Python 侧 pytest（`backend/tests/` 镜像 `backend/`），TS 侧 `frontend/tests/`（node:test + tsx）；
- [ ] README 写清：装法、提供的能力清单、所需鉴权/环境。

## 包质量红线

1. **零全局副作用**：装载期不碰网络/文件系统（发现即 import——副作用拖慢所有会话启动）；
2. **headless 可用**：交互路径全部 `has_ui` 门控降级（print/RPC 模式没有 UI）；
3. **中断友好**：长任务轮询 `signal.aborted`；
4. **状态分支安全**：扩展状态走 `append_entry` 会话条目，不写包私产文件；
5. **不遮蔽内建**：冻结形态的 `sys.path` 是 append 序——包模块名不要撞 `nova_*` 内建模块。

## 官方包的要求（想进官方目录的）

- 双语义测试：pytest（Python）+ npm test（TS）全绿；
- 二进制依赖按官方纪律（wheel pin 版本 / 托管注册表）；
- 文档：README + 各资源类目注释；
- `requires` 只指官方包或其他已发布包。

下一页：[完整教程](tutorial.md)。
