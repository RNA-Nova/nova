use std::io::Write;
use std::sync::Arc;
use std::sync::Mutex;

use ctor::ctor;
use nova_exec_server::HttpClient;
use nova_exec_server::HttpRedirectPolicy;
use nova_exec_server::HttpRequestParams;
use nova_exec_server::RouteAwareHttpClient;
use nova_exec_server_http_client::HttpClientFactory;
use nova_exec_server_http_client::OutboundProxyPolicy;
use pretty_assertions::assert_eq;
use tokio::io::AsyncBufReadExt;
use tokio::io::AsyncWriteExt;
use tokio::io::BufReader;
use tokio::net::TcpListener;
use tracing_subscriber::Layer;
use tracing_subscriber::layer::SubscriberExt;

// 每个 integration test 文件都是独立进程；ctor 在测试线程启动前把回环目标
// 固定为直连。这样 macOS 系统代理或调用方 shell 中的 HTTP(S)_PROXY 都不会
// 把“关闭端口连接失败”夹具变成代理返回的 HTTP 响应，也不依赖外层 NO_PROXY。
#[ctor]
static DIRECT_LOOPBACK_PROXY_ENV: () = {
    // SAFETY：ctor 在本测试进程的 main/测试线程启动前执行，不存在并发环境读写。
    unsafe {
        std::env::set_var("NO_PROXY", "127.0.0.1,localhost");
        std::env::set_var("no_proxy", "127.0.0.1,localhost");
    }
};

#[tokio::test(flavor = "current_thread")]
async fn delegated_http_success_logs_do_not_expose_sensitive_request_or_response_data()
-> anyhow::Result<()> {
    let log_buffer = Arc::new(Mutex::new(Vec::new()));
    let writer_buffer = Arc::clone(&log_buffer);
    let subscriber = tracing_subscriber::registry().with(
        tracing_subscriber::fmt::layer()
            .with_ansi(false)
            .with_writer(move || TestLogWriter(Arc::clone(&writer_buffer)))
            .with_filter(
                tracing_subscriber::filter::Targets::new()
                    .with_target("nova_exec_server_http_client", tracing::Level::TRACE)
                    .with_target("nova_exec_server", tracing::Level::TRACE),
            ),
    );
    let _guard = tracing::subscriber::set_default(subscriber);
    tracing::debug!(target: "nova_exec_server", "log capture sentinel");
    let client =
        RouteAwareHttpClient::new(HttpClientFactory::new(OutboundProxyPolicy::ReqwestDefault));

    for (redirect_policy, status, query_secret, cookie_secret, location_secret) in [
        (
            HttpRedirectPolicy::Follow,
            "200 OK",
            "follow-query-secret",
            "follow-cookie-secret",
            "follow-location-secret",
        ),
        (
            HttpRedirectPolicy::Stop,
            "302 Found",
            "stop-query-secret",
            "stop-cookie-secret",
            "stop-location-secret",
        ),
    ] {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await?;
        let address = listener.local_addr()?;
        let response = format!(
            "HTTP/1.1 {status}\r\nSet-Cookie: session={cookie_secret}\r\nLocation: http://127.0.0.1/private?token={location_secret}\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
        );
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await?;
            let mut reader = BufReader::new(stream);
            loop {
                let mut line = String::new();
                if reader.read_line(&mut line).await? == 0 {
                    anyhow::bail!("HTTP client disconnected before completing request headers");
                }
                if line == "\r\n" {
                    break;
                }
            }
            reader.get_mut().write_all(response.as_bytes()).await?;
            anyhow::Ok(())
        });

        let response = client
            .http_request(HttpRequestParams {
                method: "GET".to_string(),
                url: format!("http://{address}/delegated?token={query_secret}"),
                headers: Vec::new(),
                body: None,
                timeout_ms: Some(5_000),
                redirect_policy,
                request_id: "sensitive-request".to_string(),
                stream_response: false,
            })
            .await?;
        let expected_status = match redirect_policy {
            HttpRedirectPolicy::Follow => 200,
            HttpRedirectPolicy::Stop => 302,
        };
        assert_eq!(response.status, expected_status);
        server.await??;
    }

    let logs = String::from_utf8(log_buffer.lock().expect("log buffer lock").clone())?;
    assert!(logs.contains("log capture sentinel"));
    for secret in [
        "follow-query-secret",
        "follow-cookie-secret",
        "follow-location-secret",
        "stop-query-secret",
        "stop-cookie-secret",
        "stop-location-secret",
    ] {
        assert!(!logs.contains(secret), "logs exposed {secret}:\n{logs}");
    }

    Ok(())
}

#[tokio::test(flavor = "current_thread")]
async fn delegated_http_failure_warning_redacts_request_url() -> anyhow::Result<()> {
    let log_buffer = Arc::new(Mutex::new(Vec::new()));
    let writer_buffer = Arc::clone(&log_buffer);
    let subscriber = tracing_subscriber::registry().with(
        tracing_subscriber::fmt::layer()
            .with_ansi(false)
            .with_writer(move || TestLogWriter(Arc::clone(&writer_buffer)))
            .with_filter(
                tracing_subscriber::filter::Targets::new()
                    .with_target("nova_exec_server_http_client", tracing::Level::TRACE)
                    .with_target("nova_exec_server", tracing::Level::TRACE),
            ),
    );
    let _guard = tracing::subscriber::set_default(subscriber);
    let unavailable_server = std::net::TcpListener::bind(("127.0.0.1", 0))?;
    let unavailable_address = unavailable_server.local_addr()?;
    drop(unavailable_server);
    let client =
        RouteAwareHttpClient::new(HttpClientFactory::new(OutboundProxyPolicy::ReqwestDefault));

    let error = client
        .http_request(HttpRequestParams {
            method: "GET".to_string(),
            url: format!(
                "http://{unavailable_address}/private-path-secret?token=failure-query-secret"
            ),
            headers: Vec::new(),
            body: None,
            timeout_ms: None,
            redirect_policy: HttpRedirectPolicy::Follow,
            request_id: "failed-sensitive-request".to_string(),
            stream_response: false,
        })
        .await;
    assert!(error.is_err(), "request to a closed port should fail");

    let logs = String::from_utf8(log_buffer.lock().expect("log buffer lock").clone())?;
    assert!(logs.contains("http/request send failed"));
    assert!(logs.contains("error_is_connect=true"));
    for secret in ["private-path-secret", "failure-query-secret"] {
        assert!(!logs.contains(secret), "logs exposed {secret}:\n{logs}");
    }

    Ok(())
}

#[derive(Clone)]
struct TestLogWriter(Arc<Mutex<Vec<u8>>>);

impl Write for TestLogWriter {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.0
            .lock()
            .map_err(|_| std::io::Error::other("log buffer lock"))?
            .extend_from_slice(bytes);
        Ok(bytes.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
