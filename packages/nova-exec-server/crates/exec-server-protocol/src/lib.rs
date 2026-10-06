mod environment_config;
mod network_policy;
mod process_id;
mod protocol;
pub mod rpc;

pub use environment_config::*;
pub use network_policy::*;
pub use process_id::ProcessId;
pub use protocol::*;
pub use rpc::*;

/// nova-exec-server 线上协议版本（"major.minor"——major 不等即不兼容，minor 只增能力）。
///
/// 1.0 = 通用执行后端清洗后的首个协议面（process/fs/pty/environment/http）。
/// 1.1 = 新增 fs followSymlinks 选项、process/start shellSnapshot 参数与
///       readStream/writeStream/shellSnapshotV2 能力位（均为可选增量，向后兼容）。
/// 1.2 = initialize 响应捎带 environmentInfo（可选，旧服务端缺省），EnvironmentInfo
///       新增 userHomeDir/platformOs/tempDir 可选字段（均为可选增量，向后兼容）。
/// 1.3 = 托管网络代理落地：networkProxyLaunch 能力位如实宣告 true，
///       新增 network/policyDecision 审计通知（仅 process/start 携带 networkProxy
///       时由服务端发出，可选增量，向后兼容）。
/// 1.4 = 恢复 environmentConfig/read 端点（nova 语义：executor 代读本机
///       user 层 ~/.nova/exec-server/config.toml（TOML）与 project 层
///       <cwd>/.nova/settings.json（JSON），按键路径投影回传层栈，不合并不裁决），
///       environmentConfigRead 能力位回 true（可选增量，向后兼容）。
/// 1.5 = 补 httpHeaderEnvVars 能力位（http/request 的 valueEnvVar 机制早已实现，
///       仅宣告缺位——如实宣告 true；可选增量，向后兼容）。
/// 1.6 = 能力位归位：补回 sandboxedFileStreaming 约束位（fs 流式通道可按请求
///       装配沙箱执行——readStream 沙箱开门取 fd、writeStream 长命沙箱 helper，
///       如实宣告 true）；撤除 readStream/writeStream 两个端点存在位
///       （端点存在不配位——约束才配位；端点本身不变，可选增量，向后兼容）。
/// 1.7 = codex 上游 additive 跟进：EnvironmentInfo 补 executorVersion（缺省
///       "0.0.0"，服务端盖印 crate 版本）/providerId（缺省省略，nova 无
///       build-stamp 基建）/prependPathDirs（空即省略）；ExecParams 补 metadata
///       归因（存而不取）；ManagedNetworkSandboxContext 补 allowUnixSockets/
///       dangerouslyAllowAllUnixSockets（缺省受限）；ProcessSandboxType 补
///       windowsMxc 枚举值、windowsSandboxLevel 字段承载 WindowsSandboxSelection
///       （新增 "mxc" 值，实现未移植，下发即 invalid_params），删除
///       windowsSandboxPrivateDesktop 字段；客户端入站请求限 8KiB（数据面通知
///       2MiB、tracestate 512B 裁剪）；fs/open 补 mode（read|replace，replace
///       暂拒）与 fileWriteStreaming 能力位（恒 false）（全部可选增量，
///       windowsSandboxPrivateDesktop 删除一项对位 codex a633ebc124 同步执行）。
/// 1.8 = EnvironmentCapabilities 补 windowsMxc 位（对位 codex 同名位：windows 端
///       按 mxc-sandbox 可用性如实上报，非 windows 恒 false——MXC 实现本体已随
///       Windows 批次落地，此前缺位导致客户端无法发现；可选增量，向后兼容）。
/// 1.9 = 可写文件流落地（对位 codex c39bfa4c8f/d25c114d49）：fs/open 的 replace
///       模式生效（创建/截断写打开），新增 fs/writeBlock（显式 offset 定位写，
///       非空块 ≤1MiB，写区间不得超 i64 上限），fileWriteStreaming 能力位翻
///       true；fs/open|readBlock|writeBlock|close 句柄表容量有界（128/连接，
///       在飞行打开也占槽），句柄文案由 "file read handle" 泛化为 "file handle"
///       （可选增量，向后兼容——读打开语义不变）。
pub const PROTOCOL_VERSION: &str = "1.9";
