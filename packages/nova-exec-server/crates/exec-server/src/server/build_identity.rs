//! 在接受连接前缓存运行中 executor 的构建身份。
//! （对位 codex 39f4277087 `server/build_identity.rs`）
//!
//! 锚点经 codex_build_info 取 (build_commit, target)：commit 由最终二进制盖戳
//! （`STABLE_GIT_COMMIT`，未盖戳为 "dev"），target 由 build.rs 以
//! `CODEX_BUILD_TARGET` 注入编译期。nova 未移植 build-info crate 本体
//! （InstallContext/semver 面），按锚点方式补齐最小 build-stamp 基建：
//! 本 crate 的 build.rs 注入 `NOVA_EXEC_SERVER_BUILD_TARGET`，commit 读同一
//! `STABLE_GIT_COMMIT` 盖戳变量；`build_id` 算法与锚点逐字一致。

use std::sync::LazyLock;

use sha2::Digest;
use sha2::Sha256;

use crate::protocol::EnvironmentInfo;

/// 对位锚点 `BuildInfo` 的盖戳来源：构建环境供给 STABLE_GIT_COMMIT；未盖戳
/// （含 cargo 开发构建）为 "dev"——非 40 位十六进制，build_id 因此返回 None。
const BUILD_COMMIT: &str = match option_env!("STABLE_GIT_COMMIT") {
    Some(commit) => commit,
    None => "dev",
};

pub(super) static PROVIDER_ID: LazyLock<Option<String>> =
    LazyLock::new(|| build_id(BUILD_COMMIT, env!("NOVA_EXEC_SERVER_BUILD_TARGET")));

pub(super) fn local_environment_info() -> EnvironmentInfo {
    EnvironmentInfo {
        provider_id: PROVIDER_ID.clone(),
        ..super::release_version::local_environment_info()
    }
}

/// 对位 codex build-info `build_id` 逐字移植：标准构建的不透明 ID 派生自盖戳
/// commit 与编译 target。显式输入让 CI 不运行即可标识交叉编译构建；运行时调用
/// 方必须用运行中可执行文件的盖戳与 target。稳定编码为
/// `git:<full lowercase commit>:<target>` 的 SHA-256，不含版本。它标识一种标准
/// 构建配置，不是精确的可执行字节。
fn build_id(build_commit: &str, target: &str) -> Option<String> {
    if build_commit.len() != 40
        || !build_commit.bytes().all(|byte| byte.is_ascii_hexdigit())
        || target.is_empty()
    {
        return None;
    }
    let identity = format!("git:{}:{target}", build_commit.to_ascii_lowercase());
    let digest = Sha256::digest(identity.as_bytes());
    Some(format!("sha256:{digest:x}"))
}

#[cfg(test)]
mod tests {
    use super::build_id;

    const BUILD_COMMIT: &str = "0123456789abcdef0123456789abcdef01234567";

    // 对位 codex build-info `build_id_is_stable_and_uses_the_supplied_commit_and_target`
    // （测试向量同时覆盖与 CI 机器不同的 target）。
    #[test]
    fn build_id_is_stable_and_uses_the_supplied_commit_and_target() {
        for (target, digest) in [
            (
                "aarch64-apple-darwin",
                "90138d7ee35f3f61cd61eb55e8d64105f856055641af175388b102dffa772594",
            ),
            (
                "x86_64-unknown-linux-gnu",
                "89f3a373036537bde64861b3ad8b1c9494924b0174622e47acda695619630d09",
            ),
            (
                "x86_64-unknown-linux-musl",
                "fb4f62da3e84f6864dcec8ede7bc66f1c96ecaeaf55f8a786b85df994057c8ac",
            ),
        ] {
            let expected = Some(format!("sha256:{digest}"));
            assert_eq!(build_id(BUILD_COMMIT, target), expected);
            assert_eq!(
                build_id(&BUILD_COMMIT.to_ascii_uppercase(), target),
                expected
            );
            assert_ne!(
                build_id("1123456789abcdef0123456789abcdef01234567", target),
                expected
            );
        }
    }

    // 对位 codex build-info `build_id_requires_a_valid_stamp_and_target`。
    #[test]
    fn build_id_requires_a_valid_stamp_and_target() {
        for commit in [
            "",
            "dev",
            "unknown",
            "0123456",
            "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
        ] {
            assert_eq!(build_id(commit, "x86_64-unknown-linux-musl"), None);
        }
        assert_eq!(build_id(BUILD_COMMIT, ""), None);
    }
}
