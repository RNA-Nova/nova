//! nova-exec-server CLI 端到端测试（对位 codex 39f4277087
//! `cli/tests/exec_server.rs` 中适配我方 CLI 面的用例组——锚点的
//! remote/forward/strict-config 等面 nova CLI 未收，不随迁）。
//! 驱动真实编译产物 `nova-exec-server` 二进制（cargo_bin 定位）。

use std::io::BufRead as _;
use std::io::BufReader as StdBufReader;
use std::net::TcpStream;
use std::path::Path;
use std::process::Command;
use std::process::Stdio;
use std::time::Duration;
use std::time::Instant;

use anyhow::Result;
use tempfile::TempDir;

/// 定位 nova-exec-server 编译产物（cargo_bin 助手：nextest/ cargo 两种
/// target 布局都能命中）。
fn nova_exec_server_bin() -> Result<std::path::PathBuf> {
    Ok(nova_exec_server_utils_cargo_bin::cargo_bin(
        "nova-exec-server",
    )?)
}

fn nova_command(home: &Path) -> Result<Command> {
    let mut cmd = Command::new(nova_exec_server_bin()?);
    cmd.env("NOVA_EXEC_SERVER_HOME", home);
    Ok(cmd)
}

/// 等子进程在给定预算内退出；超时先杀再报错（防悬挂拖死测试进程）。
fn wait_with_timeout(
    child: &mut std::process::Child,
    budget: Duration,
) -> Result<std::process::ExitStatus> {
    let deadline = Instant::now() + budget;
    loop {
        if let Some(status) = child.try_wait()? {
            return Ok(status);
        }
        if Instant::now() > deadline {
            let _ = child.kill();
            let _ = child.wait();
            anyhow::bail!("child did not exit within {budget:?}");
        }
        std::thread::sleep(Duration::from_millis(25));
    }
}

/// 对位 codex local_exec_server_accepts_concurrent_requests_flag：
/// stdio 形态 + --concurrent-requests 2 正常起退（stdin EOF 自然收摊）。
#[test]
fn local_exec_server_accepts_concurrent_requests_flag() -> Result<()> {
    let home = TempDir::new()?;
    let status = nova_command(home.path())?
        .args(["--listen", "stdio", "--concurrent-requests", "2"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()?;
    assert!(status.success(), "{status}");
    Ok(())
}

/// 对位 codex local_exec_server_allows_disabled_parent_lifetime_environment_variable：
/// env 显式禁用父死子随后 stdio 形态正常起退。
#[test]
fn local_exec_server_allows_disabled_parent_lifetime_environment_variable() -> Result<()> {
    let home = TempDir::new()?;
    let status = nova_command(home.path())?
        .env("NOVA_EXEC_SERVER_EXIT_ON_STDIN_CLOSE", "false")
        .args(["--listen", "stdio"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()?;
    assert!(status.success(), "{status}");
    Ok(())
}

/// 对位 codex local_exec_server_exits_successfully_on_sigterm（unix）：
/// WS 监听起来后 SIGTERM 必须优雅退出（exit 0）——run_until_shutdown
/// 竞争层把信号转成 Ok 返回（此前信号走默认 disposition，exit 143）。
#[cfg(unix)]
#[test]
fn local_exec_server_exits_successfully_on_sigterm() -> Result<()> {
    let home = TempDir::new()?;
    let mut child = nova_command(home.path())?
        .args(["--listen", "ws://127.0.0.1:0"])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()?;
    let mut listen_url = String::new();
    StdBufReader::new(child.stdout.take().expect("child stdout")).read_line(&mut listen_url)?;
    assert!(listen_url.starts_with("ws://127.0.0.1:"), "{listen_url}");

    // 端口可达即就绪（listen URL 在 bind 之后才打印；再探一次确认在听）
    let listen_addr = listen_url
        .trim()
        .strip_prefix("ws://")
        .expect("listen URL should use ws://");
    let deadline = Instant::now() + Duration::from_secs(5);
    let ready = loop {
        if TcpStream::connect(listen_addr).is_ok() {
            break true;
        }
        if Instant::now() > deadline {
            break false;
        }
        std::thread::sleep(Duration::from_millis(10));
    };
    assert!(ready, "exec-server did not become ready at {listen_url}");

    // SAFETY: `child.id()` is the live process spawned above.
    let result = unsafe { libc::kill(child.id() as libc::pid_t, libc::SIGTERM) };
    assert_eq!(result, 0);
    let status = child.wait()?;
    assert!(status.success(), "{status}");
    Ok(())
}

/// WS 托管父死子随（对位 codex
/// remote_exec_server_gracefully_stops_when_parent_stdin_closes 的本地形态——
/// 锚点的 remote 注册面 nova CLI 未收）：--exit-on-stdin-close 下父侧关闭
/// stdin 管道，服务必须优雅退出（exit 0）。
#[test]
fn local_exec_server_gracefully_stops_when_parent_stdin_closes() -> Result<()> {
    let home = TempDir::new()?;
    let mut child = nova_command(home.path())?
        .args(["--listen", "ws://127.0.0.1:0", "--exit-on-stdin-close"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()?;
    let mut listen_url = String::new();
    StdBufReader::new(child.stdout.take().expect("child stdout")).read_line(&mut listen_url)?;
    assert!(listen_url.starts_with("ws://127.0.0.1:"), "{listen_url}");

    // 父侧关闭 stdin（drop 写端 → 子进程读到 EOF → leash 触发优雅退出）
    drop(child.stdin.take());

    let status = wait_with_timeout(&mut child, Duration::from_secs(15))?;
    assert!(status.success(), "{status}");
    Ok(())
}

/// 对位 codex local_exec_server_flushes_telemetry_on_stdio_disconnect：
/// 配好 [otel] metrics 出口后，stdio 断开（stdin EOF）退出前必须把
/// 队列里的指标 flush 到采集器（进程启动计数启动即发射，退出时必有载荷）。
#[tokio::test]
async fn local_exec_server_flushes_telemetry_on_stdio_disconnect() -> Result<()> {
    use wiremock::Mock;
    use wiremock::MockServer;
    use wiremock::ResponseTemplate;
    use wiremock::matchers::method;
    use wiremock::matchers::path;

    let collector = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/v1/metrics"))
        .respond_with(ResponseTemplate::new(202))
        .mount(&collector)
        .await;

    let home = TempDir::new()?;
    let collector_url = collector.uri();
    std::fs::write(
        home.path().join("config.toml"),
        format!(
            r#"
[otel]
environment = "test"
metrics_exporter = {{ OtlpHttp = {{ endpoint = "{collector_url}/v1/metrics", protocol = "Json" }} }}
"#
        ),
    )?;

    let mut child = nova_command(home.path())?
        .args(["--listen", "stdio"])
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .spawn()?;
    // 给 provider 装配与首批指标一点窗口，然后断开 stdio（EOF）
    tokio::time::sleep(Duration::from_millis(500)).await;
    drop(child.stdin.take());
    let status = wait_with_timeout(&mut child, Duration::from_secs(15))?;
    assert!(status.success(), "{status}");

    // 退出前 flush 已把指标送达采集器
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let received = collector.received_requests().await.unwrap_or_default();
        if !received.is_empty() {
            break;
        }
        anyhow::ensure!(
            Instant::now() <= deadline,
            "no metrics reached the collector"
        );
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    Ok(())
}
