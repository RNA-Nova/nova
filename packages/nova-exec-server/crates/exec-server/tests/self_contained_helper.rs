//! 不传 `--executor-linux-sandbox-exe` 时，nova-exec-server 经软链别名 +
//! argv0 分派自我分身当 Linux 沙箱 helper 的 e2e 实证
//! （对位 codex 39f4277087 arg0 自别名形态；裁点：无跨进程锁，进程私有
//! TempDir）。
#![cfg(target_os = "linux")]

use std::process::Stdio;
use std::time::Duration;

use anyhow::Context;
use anyhow::Result;
use nova_exec_server::FileSystemSandboxContext;
use nova_exec_server_protocol_core::models::PermissionProfile;
use tokio::io::AsyncBufReadExt;
use tokio::io::AsyncWriteExt;
use tokio::io::BufReader;

/// 沙箱化命令在"缺省自分身"形态下正常跑通，即证明别名+分派链完整
/// （若 argv0 basename 分派缺失，helper 会以服务器形态启动，命令不可能成功）。
#[tokio::test]
async fn linux_sandbox_self_contained_without_helper_flag() -> Result<()> {
    let Some(bwrap) = nova_exec_server_sandboxing::find_system_bwrap_in_path(
        &PermissionProfile::read_only().file_system_sandbox_policy(),
        &std::env::current_dir()?,
    ) else {
        eprintln!("skipping self-contained test: no system bubblewrap");
        return Ok(());
    };
    let available = tokio::process::Command::new(&bwrap)
        .args(["--unshare-user", "--ro-bind", "/", "/", "--", "/bin/true"])
        .output()
        .await?;
    if !available.status.success() {
        eprintln!(
            "skipping self-contained test: {}",
            String::from_utf8_lossy(&available.stderr)
        );
        return Ok(());
    }

    let mut command = tokio::process::Command::new(
        nova_exec_server_utils_cargo_bin::cargo_bin("nova-exec-server")?,
    );
    // 关键形态：不传 --executor-linux-sandbox-exe——helper 只能来自自分身。
    command
        .args(["--listen", "stdio"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    let mut child = command.spawn()?;
    let mut stdin = child.stdin.take().context("executor stdin")?;
    let mut stdout = BufReader::new(child.stdout.take().context("executor stdout")?);

    send_json_line(&mut stdin, &serde_json::json!({
        "id": 1, "method": "initialize",
        "params": {"clientName": "self-contained-test", "resumeSessionId": null}
    }))
    .await?;
    wait_for_response(&mut stdout, 1).await?;
    send_json_line(&mut stdin, &serde_json::json!({"method": "initialized", "params": {}})).await?;

    let cwd = nova_exec_server_utils_path_uri::PathUri::from_host_native_path("/tmp")?;
    let sandbox = FileSystemSandboxContext::from_permission_profile(
        PermissionProfile::read_only(),
        cwd.clone(),
    );
    send_json_line(&mut stdin, &serde_json::json!({
        "id": 2, "method": "process/start", "params": {
            "processId": "self-contained", "cwd": cwd, "sandbox": sandbox,
            "argv": ["/bin/bash", "-c", "echo SELF_CONTAINED_OK"],
            "env": {"PATH": "/usr/local/bin:/usr/bin:/bin"}, "tty": false, "pipeStdin": false, "arg0": null
        }
    }))
    .await?;

    let mut started = false;
    let mut exit_code = None;
    let mut saw_marker = false;
    let deadline = tokio::time::Instant::now() + Duration::from_secs(30);
    while tokio::time::Instant::now() < deadline && (!started || exit_code.is_none()) {
        let mut line = String::new();
        anyhow::ensure!(stdout.read_line(&mut line).await? > 0, "executor closed");
        let message: serde_json::Value = serde_json::from_str(&line)?;
        if message["id"].as_i64() == Some(2) {
            anyhow::ensure!(message.get("error").is_none(), "start failed: {message}");
            started = true;
        }
        if message["method"] == "process/output"
            && message["params"]["chunk"].as_str().is_some_and(|chunk| {
                String::from_utf8_lossy(
                    &base64_decode(chunk).unwrap_or_default(),
                )
                .contains("SELF_CONTAINED_OK")
            })
        {
            saw_marker = true;
        }
        if message["method"] == "process/exited" {
            exit_code = message["params"]["exitCode"].as_i64();
        }
    }
    assert_eq!(exit_code, Some(0));
    assert!(saw_marker, "sandboxed output marker missing");

    drop(stdin);
    let _ = child.wait().await;
    Ok(())
}

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
                "request {expected_id} failed: {message}"
            );
            return Ok(message);
        }
    }
}

// process/output 的 chunk 是 base64（对位线上契约）
fn base64_decode(input: &str) -> Result<Vec<u8>> {
    use base64::Engine;
    Ok(base64::engine::general_purpose::STANDARD.decode(input)?)
}
