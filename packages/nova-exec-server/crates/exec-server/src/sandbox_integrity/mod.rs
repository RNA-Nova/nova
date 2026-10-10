//! 沙箱完整性检查模块（对位 codex exec-server `sandbox_integrity/`，3342ee8c07 起
//! 分批落地，c2eb1f42a0 补齐 runner 与遥测）。
//!
//! 以下范围约定摘自上游 `sandbox_integrity/AGENTS.md`（按任务纪律不单独建文件）：
//!
//! - 这些检查当前只发遥测：findings 与准备错误**不得**改变沙箱构建、权限、审批或
//!   命令执行。支持的后端为 Seatbelt、bubblewrap 与 MXC；legacy Windows 不在范围内。
//! - 后端负责完整的依赖清点，并按请求的原始文件系统策略解释规则：解析命令临时目录
//!   与 MXC 卷，凡执法侧展开 deny glob 的后端都复用同一展开。runner 准备依赖事实并
//!   对每个依赖调用各检查器；检查器不感知清点与遥测。阻塞工作跑在阻塞池上，调用方
//!   等待上限 5 秒——超时即停等，不取消阻塞任务或其扫描子进程，迟到结果丢弃。
//! - 遥测从策略、依赖事实与检查结果派生**有界**标签：绝不携带文件系统路径、环境值
//!   或错误文本。缺 metrics client 只关闭上报，从不关闭检查本身；使用进程全局
//!   metrics client。
//! - 有意的近似：只兑现基本的 read/write/deny 规则，不重建原生执法、不审计 OS ACL。
//!   后端额外保护可能带来假阳性，额外写授权（如 Seatbelt scratch）可能带来假阴性；
//!   后端 glob/symlink 细节与可写硬链接别名不完全建模；独立的 glob 扫描可能与执法
//!   快照不同。指标描述观察到的检查结果，不代表准确性。
//! - MXC 扫描器发现目前用 `which`，而执法用 `Command`：搜索顺序与环境差异可能选出
//!   不同可执行文件。这是本阶段接受的遥测限制，不改生产扫描器选择。
//! - 资格（c2eb1f42a0 定稿）：`SandboxOverride::EscalatedSandboxWithRestrictions` 在
//!   准备与遥测之前跳过（这类已批准提权只保留 deny-read 限制）；普通配置的
//!   `:root = write` 加 deny 仍检查。不从策略形状推断审批。

#[cfg_attr(target_os = "linux", path = "bubblewrap.rs")]
#[cfg_attr(target_os = "macos", path = "seatbelt.rs")]
#[cfg_attr(target_os = "windows", path = "mxc.rs")]
mod backend;
mod dependencies;
mod runner;
mod telemetry;

pub use runner::run_integrity_checks;

#[cfg(test)]
#[path = "backend_tests.rs"]
mod backend_tests;
