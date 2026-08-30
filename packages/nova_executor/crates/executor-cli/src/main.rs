use anyhow::Context;
use anyhow::Result;
use clap::Parser;
use std::path::PathBuf;

use nova_executor_server::ExecServerRuntimePaths;
use nova_executor_server::RequestDispatchMode;
use nova_executor_server::run_main_with_telemetry;

/// nova-executor - 远程执行服务
#[derive(Debug, Parser)]
#[command(name = "nova-executor")]
#[command(about = "Nova remote executor service")]
struct Cli {
    /// Transport endpoint URL. Supported values: `ws://IP:PORT` (default), `wss://IP:PORT`.
    #[arg(long, value_name = "URL", default_value = "ws://127.0.0.1:8080")]
    listen: String,

    /// Maximum number of requests to process concurrently on each connection.
    /// 默认 32：Agent 客户端会并行下发工具调用（并行进程/文件操作），
    /// 默认 1 会把同一连接上的所有调用串行化。1 = 串行（Inline）模式。
    #[arg(long, value_name = "COUNT", default_value = "32")]
    concurrent_requests: usize,

    /// Path to the executor executable used to launch hidden helper modes.
    #[arg(long, value_name = "PATH")]
    executor_self_exe: Option<PathBuf>,

    /// Path to the Linux sandbox helper alias.
    #[arg(long, value_name = "PATH")]
    executor_linux_sandbox_exe: Option<PathBuf>,

    /// Enable telemetry (OpenTelemetry).
    #[arg(long)]
    telemetry: bool,
}

fn main() -> Result<()> {
    // 隐藏 helper 模式先于 clap 与 tokio 运行时分派：沙箱化 fs 操作与 arg0
    // 执行辅助分别以隐藏 flag 重启本二进制，helper 自建运行时并直接退出
    //（不能在已有 runtime 的 block_on 里再进 block_on）。
    // 注意先取出第二参数再做匹配：args.next() 会消费参数，边取边比会丢参。
    let mut args = std::env::args_os();
    let _program = args.next();
    match args.next().as_deref() {
        Some(arg) if arg == std::ffi::OsStr::new(nova_executor_server::NOVA_EXECUTOR_FS_HELPER_ARG1) => {
            nova_executor_server::run_fs_helper_main();
        }
        Some(arg)
            if arg == std::ffi::OsStr::new(nova_executor_server::NOVA_EXECUTOR_ARG0_EXEC_HELPER_ARG1) =>
        {
            nova_executor_server::run_arg0_exec_helper_main();
        }
        _ => {}
    }
    run_server()
}

#[tokio::main]
async fn run_server() -> Result<()> {
    let cli = Cli::parse();

    // 构建 runtime paths
    let executor_self_exe = cli
        .executor_self_exe
        .or_else(|| std::env::current_exe().ok())
        .context("executor self executable path is not configured")?;
    let runtime_paths =
        ExecServerRuntimePaths::new(executor_self_exe, cli.executor_linux_sandbox_exe)?;

    // 构建请求分派模式
    let request_dispatch_mode = if cli.concurrent_requests <= 1 {
        RequestDispatchMode::Inline
    } else {
        RequestDispatchMode::Concurrent {
            max_concurrent_requests: nova_executor_server::ConcurrentRequestLimit::new(
                cli.concurrent_requests,
            )
            .context("invalid concurrent requests count")?,
        }
    };

    // 本地模式：WS 回环 / stdio 承载，不做入站鉴权
    run_main_with_telemetry(
        &cli.listen,
        runtime_paths,
        nova_executor_server::ExecServerTelemetry::default(),
        nova_executor_http_client::HttpClientFactory::new(
            nova_executor_http_client::OutboundProxyPolicy::ReqwestDefault,
        ),
        request_dispatch_mode,
    )
    .await
    .map_err(|err| anyhow::anyhow!("{err}"))?;

    Ok(())
}
