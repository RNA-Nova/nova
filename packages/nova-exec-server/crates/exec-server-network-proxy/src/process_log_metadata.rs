//! Process attribution captured at launch for network policy logs.
//! Executor registration identity comes from the executor, never the controller.
//!
//! 对位 codex 39f4277087 `process_log_metadata.rs` 整文件移植。

/// Registry-issued identity of the executor that launched a process.
#[derive(Clone)]
pub struct ExecutorLogIdentity {
    pub environment_id: String,
    pub registration_id: String,
}

/// Correlation metadata only; these values do not authorize network requests.
#[derive(Clone, Default)]
pub struct NetworkProxyProcessLogMetadata {
    pub thread_id: Option<String>,
    pub tool_call_id: Option<String>,
    pub executor_identity: Option<ExecutorLogIdentity>,
}
