//! nova-exec-server WS 入站鉴权端到端测试（对位 codex 39f4277087
//! `cli/tests/exec_server_websocket_auth.rs` 中适配我方 CLI 面的用例组——
//! 驱动真实编译产物与真实 WebSocket 握手；锚点的 app-server/remote 面
//! 用例不随迁）。

use anyhow::Context;
use anyhow::Result;
use futures::SinkExt;
use futures::StreamExt;
use pretty_assertions::assert_eq;
use serde_json::json;
use sha2::Digest;
use sha2::Sha256;
use std::process::Stdio;
use std::time::Duration;
use std::time::SystemTime;
use std::time::UNIX_EPOCH;
use tempfile::TempDir;
use tokio::io::AsyncBufReadExt;
use tokio::io::BufReader;
use tokio::process::Command;
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::Error as WebSocketError;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;

enum AuthMode {
    TokenHash,
    TokenFile,
    SignedBearer,
}

fn nova_exec_server_bin() -> Result<std::path::PathBuf> {
    Ok(nova_exec_server_utils_cargo_bin::cargo_bin(
        "nova-exec-server",
    )?)
}

/// 对位 codex exec_server_websocket_auth_gates_rpc_connections：
/// 三种鉴权模式下——无凭证/错凭证在升级握手即 401（RPC 会话不会开）；
/// 正确凭证每次连接（含重连）都能完成 initialize。
#[tokio::test]
async fn exec_server_websocket_auth_gates_rpc_connections() -> Result<()> {
    tokio::time::timeout(Duration::from_secs(/*secs*/ 60), async {
        let nova = nova_exec_server_bin()?;
        for mode in [
            AuthMode::TokenHash,
            AuthMode::TokenFile,
            AuthMode::SignedBearer,
        ] {
            let home = TempDir::new()?;
            let secret = "0123456789abcdef0123456789abcdef";
            let secret_file = home.path().join("secret");
            std::fs::write(&secret_file, secret)?;
            let mut command = Command::new(&nova);
            command
                .args(["--listen", "ws://127.0.0.1:0", "--ws-auth"])
                .env("NOVA_EXEC_SERVER_HOME", home.path())
                .stdin(Stdio::null())
                .stdout(Stdio::piped())
                .stderr(Stdio::inherit())
                .kill_on_drop(true);
            let token = match mode {
                AuthMode::TokenHash => {
                    command.args([
                        "capability-token",
                        "--ws-token-sha256",
                        &format!("{:x}", Sha256::digest(secret.as_bytes())),
                    ]);
                    secret.to_string()
                }
                AuthMode::TokenFile => {
                    command
                        .args(["capability-token", "--ws-token-file"])
                        .arg(&secret_file);
                    secret.to_string()
                }
                AuthMode::SignedBearer => {
                    command
                        .args(["signed-bearer-token", "--ws-shared-secret-file"])
                        .arg(&secret_file)
                        .args([
                            "--ws-issuer",
                            "test-issuer",
                            "--ws-audience",
                            "test-executor",
                        ]);
                    let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
                    jsonwebtoken::encode(
                        &jsonwebtoken::Header::new(jsonwebtoken::Algorithm::HS256),
                        &json!({"exp": now + 300, "iss": "test-issuer", "aud": "test-executor"}),
                        &jsonwebtoken::EncodingKey::from_secret(secret.as_bytes()),
                    )?
                }
            };
            let mut child = command.spawn()?;
            let mut lines = BufReader::new(child.stdout.take().context("stdout")?).lines();
            let url = loop {
                let line = lines
                    .next_line()
                    .await?
                    .context("listener exited before startup")?;
                if line.starts_with("ws://") {
                    break line;
                }
            };

            // 缺失/错误凭证必须在任何 RPC 会话开启之前被拒
            let digest_as_bearer = format!("Bearer {:x}", Sha256::digest(secret.as_bytes()));
            for authorization in [
                None,
                Some("Bearer incorrect"),
                Some("Basic incorrect"),
                Some(digest_as_bearer.as_str()),
            ] {
                let mut request = url.as_str().into_client_request()?;
                if let Some(authorization) = authorization {
                    request
                        .headers_mut()
                        .insert("authorization", authorization.parse()?);
                }
                let error = connect_async(request)
                    .await
                    .expect_err("reject unauthorized upgrade");
                let WebSocketError::Http(response) = error else {
                    anyhow::bail!("expected HTTP rejection, got {error}");
                };
                assert_eq!(response.status(), http::StatusCode::UNAUTHORIZED);
            }

            // 鉴权对每条连接生效（含重连）
            for _ in 0..2 {
                let mut request = url.as_str().into_client_request()?;
                request
                    .headers_mut()
                    .insert("authorization", format!("Bearer {token}").parse()?);
                let (mut websocket, _) = connect_async(request).await?;
                websocket
                    .send(Message::Text(
                        json!({
                            "id": 1,
                            "method": "initialize",
                            "params": {"clientName": "auth-test"}
                        })
                        .to_string()
                        .into(),
                    ))
                    .await?;
                let response = websocket.next().await.context("initialize response")??;
                let response: serde_json::Value = serde_json::from_str(response.to_text()?)?;
                assert_eq!(response["id"], json!(1));
                assert!(response.get("error").is_none(), "{response}");
                let _ = websocket.close(None).await;
            }

            child.kill().await?;
        }
        anyhow::Ok(())
    })
    .await??;
    Ok(())
}

/// 对位 codex exec_server_websocket_auth_rejects_inapplicable_transports：
/// stdio 承载与入站 WS 鉴权互斥——装配即失败（nova CLI 无 --remote/forward
/// 面，那两个锚点分支不随迁）。
#[test]
fn exec_server_websocket_auth_rejects_inapplicable_transports() -> Result<()> {
    let home = TempDir::new()?;
    let nova = nova_exec_server_bin()?;
    for transport in [vec!["--listen", "stdio"], vec!["--listen", "stdio://"]] {
        let output = std::process::Command::new(&nova)
            .env("NOVA_EXEC_SERVER_HOME", home.path())
            .args([
                "--ws-auth",
                "capability-token",
                "--ws-token-sha256",
                &"ab".repeat(32),
            ])
            .args(transport)
            .output()?;
        assert!(
            !output.status.success(),
            "expected failure, got {:?}",
            output.status
        );
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(stderr.contains("not stdio"), "unexpected stderr: {stderr}");
    }
    Ok(())
}

/// 对位 codex exec_server_websocket_auth_rejects_invalid_configuration：
/// 鉴权旗标的非法组合在装配期即报错（错误文案指引明确）。
#[test]
fn exec_server_websocket_auth_rejects_invalid_configuration() -> Result<()> {
    let home = TempDir::new()?;
    let nova = nova_exec_server_bin()?;
    for (args, expected) in [
        (vec!["--ws-token-sha256", "invalid"], "require `--ws-auth"),
        (
            vec![
                "--ws-auth",
                "capability-token",
                "--ws-token-sha256",
                "invalid",
            ],
            "64-character hex",
        ),
        (vec!["--ws-auth", "capability-token"], "is required"),
    ] {
        let output = std::process::Command::new(&nova)
            .env("NOVA_EXEC_SERVER_HOME", home.path())
            .args(args)
            .output()?;
        assert!(
            !output.status.success(),
            "expected failure, got {:?}",
            output.status
        );
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(stderr.contains(expected), "unexpected stderr: {stderr}");
    }
    Ok(())
}
