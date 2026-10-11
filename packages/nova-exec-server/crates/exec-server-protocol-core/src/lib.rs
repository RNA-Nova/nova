pub mod config_types;
pub mod error;
pub mod exec_output;
pub mod network_policy;
pub mod models;
pub mod permissions;
pub mod protocol;
// 对位 codex 622e9e3696：sandbox 模块（SandboxOverride 住所）
pub mod sandbox;
pub mod shell_environment;

// 对位 codex 39f4277087：permission_profile_intersection / permission_profile_snapshot
// 为私有模块、经再导出暴露；request_permissions 为公开模块。
mod permission_profile_intersection;
mod permission_profile_snapshot;
pub mod request_permissions;

pub use permission_profile_intersection::PermissionIntersectionError;
pub use permission_profile_intersection::intersect_effective_permission_profiles;
