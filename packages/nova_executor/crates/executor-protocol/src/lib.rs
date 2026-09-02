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

/// nova-executor 线上协议版本（"major.minor"——major 不等即不兼容，minor 只增能力）。
///
/// 1.0 = 通用执行后端清洗后的首个协议面（process/fs/pty/environment/http）。
pub const PROTOCOL_VERSION: &str = "1.0";
