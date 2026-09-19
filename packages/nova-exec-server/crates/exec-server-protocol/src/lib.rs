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
pub const PROTOCOL_VERSION: &str = "1.6";
