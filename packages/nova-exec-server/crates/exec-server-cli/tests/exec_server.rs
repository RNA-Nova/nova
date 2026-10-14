//! nova-exec-server CLI 端到端测试（对位 codex 39f4277087
//! `cli/tests/exec_server.rs` 中适配我方 CLI 面的用例组——锚点的
//! remote/forward/strict-config 等面 nova CLI 未收，不随迁）。
//! 驱动真实编译产物 `nova-exec-server` 二进制（cargo_bin 定位）。

use std::io::BufRead as _;
use std::io::BufReader as StdBufReader;
use std::io::Read as _;
use std::io::Write as _;
use std::net::TcpStream;
use std::path::Path;
use std::process::Command;
use std::process::Stdio;
use std::time::Duration;
use std::time::Instant;

use anyhow::Context as _;
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
        .env(
            nova_exec_server::NOVA_EXEC_SERVER_EXIT_ON_STDIN_CLOSE_ENV_VAR,
            "false",
        )
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

    // 就绪门对位锚点：GET /readyz 返 200 才算就绪（裸 TCP connect 只证明
    // accept 在听，不代表 HTTP 路由已装配）。
    let listen_addr = listen_url
        .trim()
        .strip_prefix("ws://")
        .expect("listen URL should use ws://")
        .parse()?;
    let deadline = Instant::now() + Duration::from_secs(5);
    let mut ready = false;
    while let Some(remaining) = deadline.checked_duration_since(Instant::now()) {
        if let Ok(mut stream) =
            TcpStream::connect_timeout(&listen_addr, remaining.min(Duration::from_millis(100)))
        {
            let _ = stream.set_read_timeout(Some(Duration::from_secs(1)));
            let request =
                format!("GET /readyz HTTP/1.1\r\nHost: {listen_addr}\r\nConnection: close\r\n\r\n");
            let mut response = String::new();
            if stream.write_all(request.as_bytes()).is_ok()
                && stream.read_to_string(&mut response).is_ok()
                && response.starts_with("HTTP/1.1 200")
            {
                ready = true;
                break;
            }
        }
        std::thread::sleep(Duration::from_millis(10));
    }
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

    // 关 stdin 前先确认服务活着（对位锚点 try_wait 反向断言——leash 极性
    // 反转类 bug 下服务可能早已退出，缺此断言测试会假绿）。
    anyhow::ensure!(
        child.try_wait()?.is_none(),
        "exec-server exited while its parent stdin remained open"
    );

    // 父侧关闭 stdin（drop 写端 → 子进程读到 EOF → leash 触发优雅退出）
    drop(child.stdin.take());

    let status = wait_with_timeout(&mut child, Duration::from_secs(15))?;
    assert!(status.success(), "{status}");
    Ok(())
}

/// 对位 codex local_exec_server_flushes_telemetry_on_stdio_disconnect（全形态）：
/// 配好 [otel] metrics 出口后，走完整 initialize + process/start 握手再断开
/// stdio——退出前 flush 必须把**服务端遥测接线**产生的具名指标送达采集器。
/// 只断言"收到任意请求"不够：进程启动计数不经 ExecServerTelemetry 接线，
/// 接线断（inner=None）也能收到——必须断言经接线产生的指标点。
#[tokio::test]
async fn local_exec_server_flushes_telemetry_on_stdio_disconnect() -> Result<()> {
    use tokio::io::AsyncReadExt;
    use tokio::io::BufReader;
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

    let cwd = url::Url::from_directory_path(std::env::current_dir()?)
        .map_err(|()| anyhow::anyhow!("could not convert cwd to file URL"))?
        .to_string();
    #[cfg(windows)]
    let argv = vec!["ping.exe", "-n", "61", "127.0.0.1"];
    #[cfg(not(windows))]
    let argv = vec!["/bin/sleep", "60"];
    let bin = nova_exec_server_bin()?;
    let home_path = home.path().to_path_buf();
    let subprocess = async move {
        let mut command = tokio::process::Command::new(bin);
        command
            .env("NOVA_EXEC_SERVER_HOME", &home_path)
            .env("NO_PROXY", "127.0.0.1,localhost")
            .env("no_proxy", "127.0.0.1,localhost")
            .args(["--listen", "stdio"])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .kill_on_drop(true);
        let mut child = command.spawn()?;
        let mut stdin = child.stdin.take().context("exec-server stdin was not piped")?;
        let stdout = child.stdout.take().context("exec-server stdout was not piped")?;
        let mut stdout = BufReader::new(stdout);
        send_json_line(
            &mut stdin,
            &serde_json::json!({
                "id": 1,
                "method": "initialize",
                "params": {"clientName": "otel-test", "resumeSessionId": null}
            }),
        )
        .await?;
        wait_for_response(&mut stdout, /*expected_id*/ 1).await?;
        send_json_line(
            &mut stdin,
            &serde_json::json!({"method": "initialized", "params": {}}),
        )
        .await?;
        send_json_line(
            &mut stdin,
            &serde_json::json!({
                "id": 2,
                "method": "process/start",
                "params": {
                    "processId": "otel-process",
                    "argv": argv,
                    "cwd": cwd,
                    "env": {},
                    "tty": false,
                    "pipeStdin": false,
                    "arg0": null
                }
            }),
        )
        .await?;
        wait_for_response(&mut stdout, /*expected_id*/ 2).await?;
        drop(stdin);
        let mut remaining_stdout = String::new();
        stdout.read_to_string(&mut remaining_stdout).await?;
        let status = child.wait().await?;
        anyhow::ensure!(
            status.success(),
            "exec-server exited with {status}; remaining stdout: {remaining_stdout}"
        );
        Ok::<(), anyhow::Error>(())
    };
    let subprocess_result = tokio::time::timeout(Duration::from_secs(30), subprocess)
        .await
        .map_err(|_| anyhow::anyhow!("exec-server subprocess timed out"))?;
    subprocess_result?;

    let requests = collector
        .received_requests()
        .await
        .ok_or_else(|| anyhow::anyhow!("failed to read OTLP collector requests"))?;
    let metrics = requests
        .iter()
        .filter(|request| request.url.path() == "/v1/metrics")
        .map(|request| serde_json::from_slice::<serde_json::Value>(&request.body))
        .collect::<serde_json::Result<Vec<_>>>()?;
    assert_metric_point(
        &metrics,
        "exec_server_connections_active",
        &[("transport", "stdio")],
        Some(0),
    );
    assert_metric_point(
        &metrics,
        "exec_server_connections_total",
        &[("transport", "stdio")],
        Some(1),
    );
    assert_metric_point(
        &metrics,
        "exec_server_requests_total",
        &[("method", "process/start"), ("result", "success")],
        Some(1),
    );
    assert_metric_point(&metrics, "exec_server_processes_active", &[], Some(0));
    assert_metric_point(
        &metrics,
        "exec_server_processes_finished_total",
        &[("result", "terminated")],
        Some(1),
    );
    assert_metric_point(
        &metrics,
        "exec_server_request_duration_seconds",
        &[("method", "process/start"), ("result", "success")],
        /*value*/ None,
    );
    assert_metric_point(
        &metrics,
        "exec_server_process_duration_seconds",
        &[("result", "terminated")],
        /*value*/ None,
    );
    Ok(())
}

async fn send_json_line(
    stdin: &mut (impl tokio::io::AsyncWrite + Unpin),
    message: &serde_json::Value,
) -> Result<()> {
    use tokio::io::AsyncWriteExt;
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
    use tokio::io::AsyncBufReadExt;
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

/// 对位 codex assert_metric_point：在 OTLP JSON 载荷里按 指标名 + 属性集
/// （+可选值）找数据点；值 None = 只断言存在（直方图）。
fn assert_metric_point(
    payloads: &[serde_json::Value],
    name: &str,
    attributes: &[(&str, &str)],
    value: Option<i64>,
) {
    let found = payloads
        .iter()
        .flat_map(|payload| payload["resourceMetrics"].as_array().into_iter().flatten())
        .flat_map(|resource| resource["scopeMetrics"].as_array().into_iter().flatten())
        .flat_map(|scope| scope["metrics"].as_array().into_iter().flatten())
        .filter(|metric| metric["name"].as_str() == Some(name))
        .flat_map(|metric| {
            ["gauge", "sum", "histogram"]
                .into_iter()
                .find_map(|kind| metric[kind]["dataPoints"].as_array())
                .into_iter()
                .flatten()
        })
        .any(|point| {
            let actual_attributes = point["attributes"]
                .as_array()
                .map(Vec::as_slice)
                .unwrap_or_default();
            let attributes_match = actual_attributes.len() == attributes.len()
                && attributes.iter().all(|(expected_key, expected_value)| {
                    actual_attributes.iter().any(|actual| {
                        actual["key"].as_str() == Some(*expected_key)
                            && actual["value"]["stringValue"].as_str() == Some(*expected_value)
                    })
                });
            let actual_value = point["asInt"]
                .as_i64()
                .or_else(|| point["asInt"].as_str()?.parse().ok());
            attributes_match && value.is_none_or(|expected| actual_value == Some(expected))
        });
    assert!(
        found,
        "metric {name} with attributes {attributes:?} and value {value:?} missing"
    );
}
