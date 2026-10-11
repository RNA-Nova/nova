//! End-to-end checks that only executor startup can opt into PID inheritance.
//!
//! 对位 codex 39f4277087 `codex-rs/cli/tests/exec_server/pid_namespace_tests.rs`。
//! 结构差异：codex 是单二进制（`codex exec-server` 子命令 + arg0 分派 helper），
//! nova 拆为 `nova-exec-server`（平面 CLI，无子命令）与 `nova-linux-sandbox`
//! 两个二进制——后者经 `--executor-linux-sandbox-exe` 传给 executor。
#![cfg(target_os = "linux")]

use std::process::Stdio;
use std::time::Duration;

use anyhow::Context;
use anyhow::Result;
use nova_exec_server::FileSystemSandboxContext;
use nova_exec_server_protocol_core::models::PermissionProfile;
use nova_exec_server_protocol_core::permissions::FileSystemAccessMode;
use nova_exec_server_protocol_core::permissions::FileSystemPath;
use nova_exec_server_protocol_core::permissions::FileSystemSandboxEntry;
use nova_exec_server_protocol_core::permissions::FileSystemSandboxPolicy;
use nova_exec_server_protocol_core::permissions::FileSystemSpecialPath;
use nova_exec_server_protocol_core::permissions::NetworkSandboxPolicy;
use nova_exec_server_utils_absolute_path::AbsolutePathBuf;
use nova_exec_server_utils_path_uri::PathUri;
use pretty_assertions::assert_eq;
use tempfile::TempDir;
use tokio::io::AsyncBufReadExt;
use tokio::io::AsyncReadExt;
use tokio::io::AsyncWriteExt;
use tokio::io::BufReader;

#[tokio::test]
async fn pid_inheritance_is_startup_only_for_process_and_filesystem_helpers() -> Result<()> {
    let Some(bwrap) = nova_exec_server_sandboxing::find_system_bwrap_in_path(
        &PermissionProfile::read_only().file_system_sandbox_policy(),
        &std::env::current_dir()?,
    ) else {
        eprintln!("skipping PID namespace test: no system bubblewrap");
        return Ok(());
    };
    let available = tokio::process::Command::new(&bwrap)
        .args(["--unshare-user", "--ro-bind", "/", "/", "--", "/bin/true"])
        .output()
        .await?;
    if !available.status.success() {
        eprintln!(
            "skipping PID namespace test: {}",
            String::from_utf8_lossy(&available.stderr)
        );
        return Ok(());
    }
    tokio::time::timeout(Duration::from_secs(30), async {
        let fixture = TempDir::new()?;
        let wrapper = fixture.path().join("bwrap");
        std::os::unix::fs::symlink(bwrap, fixture.path().join("real-bwrap"))?;
        nova_exec_server_utils_cargo_bin::write_executable(&wrapper, r#"#!/bin/sh
for arg in "$@"; do
    [ "$arg" = "--" ] && break
    if [ "$arg" = "--proc" ]; then
        echo "bwrap: Can't mount proc on /newroot/proc: Operation not permitted" >&2
        exit 1
    fi
done
exec "${0%/*}/real-bwrap" "$@"
"#)?;
        let path = format!("{}:{}", fixture.path().display(), std::env::var("PATH")?);
        // System bubblewrap discovery intentionally excludes executables under the command cwd.
        let workspace = TempDir::new()?;
        let file = workspace.path().join("readable");
        std::fs::write(&file, "ok")?;
        let file_uri = PathUri::from_host_native_path(&file)?;
        let cwd = PathUri::from_host_native_path(workspace.path())?;
        let minimal_policy = FileSystemSandboxPolicy::restricted(vec![
            FileSystemSandboxEntry::new(
                FileSystemPath::Special { value: FileSystemSpecialPath::Minimal },
                FileSystemAccessMode::Read,
            ),
            FileSystemSandboxEntry::new(
                AbsolutePathBuf::try_from(workspace.path())?.into(), FileSystemAccessMode::Read,
            ),
            FileSystemSandboxEntry::new(
                AbsolutePathBuf::from_absolute_path("/proc/version")?.into(), FileSystemAccessMode::Deny,
            ),
        ]);
        let minimal_profile = PermissionProfile::from_runtime_permissions(
            &minimal_policy, NetworkSandboxPolicy::Restricted,
        );
        for (inherit, profile) in [
            (false, PermissionProfile::read_only()),
            (true, PermissionProfile::read_only()),
            (true, minimal_profile),
        ] {
            let minimal = profile.file_system_sandbox_policy().include_platform_defaults();
            let sandbox = FileSystemSandboxContext::from_permission_profile(
                profile, cwd.clone(),
            );
            let nova_exec_server_home = TempDir::new()?;
            // Neither a config key nor executor/request environment variables may opt in.
            // （nova CLI 无该配置键与 env 绑定，此处断言两者天然不生效）
            std::fs::write(nova_exec_server_home.path().join("config.toml"),
                "linux_sandbox_pid_namespace = 'inherit'\n")?;
            let mut command = tokio::process::Command::new(
                nova_exec_server_utils_cargo_bin::cargo_bin("nova-exec-server")?);
            command.args(["--listen", "stdio"])
                // nova 结构差异：linux 沙箱 helper 是独立二进制（codex 为 arg0 重入
                // 单二进制），经启动旗标显式传给 executor。
                .arg(format!(
                    "--executor-linux-sandbox-exe={}",
                    nova_exec_server_utils_cargo_bin::cargo_bin("nova-linux-sandbox")?.display()
                ))
                .env("NOVA_EXEC_SERVER_HOME", nova_exec_server_home.path())
                .env("PATH", &path)
                .env("NOVA_EXEC_SERVER_LINUX_SANDBOX_PID_NAMESPACE", "inherit")
                .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped())
                .kill_on_drop(true);
            if inherit {
                command.arg("--linux-sandbox-pid-namespace=inherit");
            }
            let mut child = command.spawn()?;
            let mut stdin = child.stdin.take().context("executor stdin")?;
            let mut stdout = BufReader::new(child.stdout.take().context("executor stdout")?);
            send_json_line(&mut stdin, &serde_json::json!({
                "id": 1, "method": "initialize",
                "params": {"clientName": "pid-namespace-test", "resumeSessionId": null}
            })).await?;
            wait_for_response(&mut stdout, /*expected_id*/ 1).await?;
            send_json_line(&mut stdin, &serde_json::json!({"method": "initialized", "params": {}})).await?;
            send_json_line(&mut stdin, &serde_json::json!({
                "id": 2, "method": "process/start", "params": {
                    "processId": "pid-test", "cwd": cwd, "sandbox": sandbox,
                    "argv": ["/bin/bash", "-lc", r#"
set -e
IFS=' ' read -r pid rest < /proc/self/stat
test "$$" = "$pid"
test "$(cat <(printf shell-ok))" = shell-ok
if [ "$MINIMAL" = true ]; then
    ! cat /proc/version
fi
"#],
                    "env": {"PATH": path, "MINIMAL": minimal.to_string(), "NOVA_EXEC_SERVER_LINUX_SANDBOX_PID_NAMESPACE": "inherit"},
                    "tty": false, "pipeStdin": false, "arg0": null
                }
            })).await?;
            // Notifications may precede the process/start response, so consume both.
            let mut started = false;
            let mut exit_code = None;
            while !started || exit_code.is_none() {
                let mut line = String::new();
                anyhow::ensure!(stdout.read_line(&mut line).await? > 0, "executor closed");
                let message: serde_json::Value = serde_json::from_str(&line)?;
                if message["id"] == 2 {
                    anyhow::ensure!(message.get("error").is_none(), "start failed: {message}");
                    started = true;
                }
                if message["method"] == "process/exited" {
                    exit_code = message["params"]["exitCode"].as_i64();
                }
            }
            assert_eq!(exit_code, Some(if inherit { 0 } else { 1 }));
            send_json_line(&mut stdin, &serde_json::json!({
                "id": 3, "method": "fs/readFile", "params": {"path": file_uri, "sandbox": sandbox}
            })).await?;
            loop {
                let mut line = String::new();
                anyhow::ensure!(stdout.read_line(&mut line).await? > 0, "executor closed");
                let message: serde_json::Value = serde_json::from_str(&line)?;
                if message["id"] != 3 { continue; }
                assert_eq!(message["result"], serde_json::json!({"dataBase64": "b2s="}));
                break;
            }
            drop(stdin);
            // Drain notifications while the executor shuts down.
            let mut remaining = String::new();
            stdout.read_to_string(&mut remaining).await?;
            let output = child.wait_with_output().await?;
            anyhow::ensure!(output.status.success(), "executor failed: {}", String::from_utf8_lossy(&output.stderr));
        }
        Ok(())
    }).await?
}

// 对位 codex 39f4277087 `cli/tests/exec_server.rs:627` 同名 helper
async fn send_json_line(
    stdin: &mut (impl tokio::io::AsyncWrite + Unpin),
    message: &serde_json::Value,
) -> Result<()> {
    let mut encoded = serde_json::to_vec(message)?;
    encoded.push(b'\n');
    stdin.write_all(&encoded).await?;
    stdin.flush().await?;
    Ok(())
}

// 对位 codex 39f4277087 `cli/tests/exec_server.rs:686` 同名 helper
async fn wait_for_response(
    stdout: &mut (impl tokio::io::AsyncBufRead + Unpin),
    expected_id: i64,
) -> Result<serde_json::Value> {
    loop {
        let mut line = String::new();
        if stdout.read_line(&mut line).await? == 0 {
            anyhow::bail!("exec-server stdout closed before response {expected_id}");
        }
        let message: serde_json::Value = serde_json::from_str(&line)?;
        if message["id"].as_i64() == Some(expected_id) {
            anyhow::ensure!(
                message.get("error").is_none(),
                "exec-server request {expected_id} failed: {message}"
            );
            return Ok(message);
        }
    }
}
