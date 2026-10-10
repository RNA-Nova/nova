# codex 上游同步锚点

> nova-exec-server 对齐 codex exec-server 的同步基线与节奏记录。

## 当前锚点

- **commit**：`39f427708738953ac01c40c3d9351236e8ea21c0`
- **上游时间**：2026-10-08 20:49 +0000
- **nova 侧状态**：追新批至此锚点全部清零（批次 1+2 十四项安全修复、A 档七件、
  spawn 管线换代、C+D 完整性全链、d6fb836f31 尾巴，协议 v1.13，CI 八腿全绿）。
  剩余仅 MXC SDK 1.0.0（外部 crate 依赖决策）一项刻意排期。

## 同步节奏

- **不逐日追新**：上游每天数十提交，逐日追会把对齐工程变成永远还不完的债。
- **每周一次对账**（或有大版本动作时），一次对账 = fetch + 区间分诊 + 批次移植
  + 对照表刷新 + 锚点前移。
- 每个移植批次的提交信息标注对位 hash；锚点前移时更新本文件。

## 对账方法（沉淀自批次 1+2 的教训，逐条执行防漏）

1. `git -C <codex> fetch origin main` 后，区间清单：
   `git log --no-merges <旧锚点>..origin/main -- <镜像路径集>`（镜像路径集 = 各
   crate 对应目录 + `protocol/src/permissions*`/`sandbox.rs` 等子集镜像点）。
2. **逐提交读 diff**（`git show <hash>`），不允许只读提交说明/统计——分诊结论
   必须来自补丁本体（历史教训：只看说明曾把顶层落点误判为 legacy 平铺）。
3. 每项标注：直移 / 适配（注裁点） / 不适用（未镜像面） / 决策项（给用户拍板）。
4. **crate 级文件清单 diff** 兜底（`git ls-tree -r` vs `find`），防"crate 在但
   模块漏镜像"的粒度漏检。
5. 移植纪律：以 codex 代码为唯一准绳、逐 hunk 对位、不发明、不做范围外修复；
   nova 自有取舍（命名映射/裁点）在注释标"对位 codex <hash>"并注明理由。
6. 验证链：本机 cargo test（合并态）→ zig windows-gnu 交叉编译 → 245 服务器
   Linux 实测（bwrap 族）→ py 双包测试 → CI 八腿逐腿核实。
