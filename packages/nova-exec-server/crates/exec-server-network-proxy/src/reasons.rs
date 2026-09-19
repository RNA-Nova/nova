//! 策略拒绝原因常量（移植自 codex network-proxy `reasons.rs`）。
//!
//! 裁点：MITM 面第一期未移植，`REASON_MITM_HOOK_DENIED` / `REASON_MITM_REQUIRED`
//! 两个仅被 MITM 路径引用的常量随之裁掉。

pub(crate) const REASON_DENIED: &str = "denied";
pub(crate) const REASON_METHOD_NOT_ALLOWED: &str = "method_not_allowed";
pub(crate) const REASON_NOT_ALLOWED: &str = "not_allowed";
pub(crate) const REASON_NOT_ALLOWED_LOCAL: &str = "not_allowed_local";
pub(crate) const REASON_POLICY_DENIED: &str = "policy_denied";
pub(crate) const REASON_PROXY_DISABLED: &str = "proxy_disabled";
pub(crate) const REASON_UNIX_SOCKET_UNSUPPORTED: &str = "unix_socket_unsupported";
