//! 在执行端元数据中带上启动时解析的包发布版本（对位 codex
//! `server/release_version.rs`；provider_id 需要 build-stamp 基建，nova 未引入，
//! 保持线上缺省 None）。

use crate::protocol::EnvironmentInfo;

/// 版本取自本 crate 编译期版本号，进程生命周期内恒定（对位 codex 的
/// 启动时缓存语义；`env!` 即编译期常量）。
pub(super) fn local_environment_info() -> EnvironmentInfo {
    EnvironmentInfo {
        executor_version: env!("CARGO_PKG_VERSION").to_string(),
        ..EnvironmentInfo::local()
    }
}

#[cfg(test)]
mod tests {
    use super::local_environment_info;

    #[test]
    fn local_environment_info_reports_crate_release_version() {
        let info = local_environment_info();

        assert_eq!(info.executor_version, env!("CARGO_PKG_VERSION"));
        assert_eq!(
            serde_json::to_value(&info).expect("environment info should serialize")
                ["executorVersion"],
            serde_json::json!(env!("CARGO_PKG_VERSION"))
        );
    }
}
