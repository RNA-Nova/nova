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
    /// Transport endpoint URL. Supported values: `ws://IP:PORT` (default), `stdio`/`stdio://`.
    /// （wss 不支持：服务端只做回环承载，TLS 归上层隧道/中继层）
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

    /// 父死子随：WS 托管 spawn 场景下，父进程持有的 stdin 管道关闭即退出
    /// （对位 codex --exit-on-stdin-close / ParentLifetime::StdinPipe）。
    /// stdio 形态下 stdin 本就是传输线，EOF 自然结束服务，此旗标为 no-op。
    #[arg(long, env = "NOVA_EXECUTOR_EXEC_SERVER_EXIT_ON_STDIN_CLOSE")]
    exit_on_stdin_close: bool,
}

/// 安装最小 stderr 日志（EnvFilter 读 RUST_LOG，默认 warn）——此前服务端全部
/// tracing 日志在生产被静默丢弃（无任何 subscriber），这是补齐而非新特性。
fn install_stderr_logging() {
    let filter = tracing_subscriber::EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("warn"));
    let _ = tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_writer(std::io::stderr)
        .try_init(); // 重复 init（测试嵌入场景）不报错
}

/// 父死子随监视线程：父进程持有的 stdin 管道 EOF 即退出（对位 codex 的
/// io::copy(stdin→sink) 完成语义——copy 返回时父已死）。
fn spawn_stdin_lifetime_leash() {
    std::thread::spawn(|| {
        let _ = std::io::copy(&mut std::io::stdin().lock(), &mut std::io::sink());
        tracing::info!("parent stdin pipe closed; exiting");
        std::process::exit(0);
    });
}

/// 遥测装配（codex build_provider 对位）：读 executor 配置根 config.toml 的
/// [otel] 段 → 装全局 provider。无配置/无出口 = None（静默 noop）；
/// 配置损坏/装配失败 = warn 后 None（响亮但不炸服务）。
fn assemble_otel_provider() -> Option<nova_executor_otel::OtelProvider> {
    let home = match nova_executor_utils_home_dir::find_nova_executor_home() {
        Ok(home) => home,
        Err(error) => {
            tracing::warn!(%error, "executor home 解析失败，遥测按静默 noop 继续");
            return None;
        }
    };
    let settings = match nova_executor_otel::load_otel_settings(home.as_path()) {
        Ok(settings) => settings,
        Err(error) => {
            tracing::warn!(%error, "[otel] 配置读取失败，遥测按静默 noop 继续");
            return None;
        }
    };
    match nova_executor_otel::OtelProvider::try_new(&settings) {
        Ok(provider) => provider,
        Err(error) => {
            tracing::warn!(%error, "OTEL provider 装配失败，遥测按静默 noop 继续");
            None
        }
    }
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
    install_stderr_logging();
    let cli = Cli::parse();

    // 父死子随：WS 托管场景挂 stdin 监视（stdio 形态 stdin 是传输线，
    // EOF 已由传输层自然结束服务）
    if cli.exit_on_stdin_close && !cli.listen.starts_with("stdio") {
        spawn_stdin_lifetime_leash();
    }

    // 遥测装配：executor 配置根 config.toml 的 [otel] 段配了 metrics 出口则
    // 装全局 provider（对位 codex build_provider 的配置→装配形状）；
    // 没配 = 全静默 noop（零外发，可选语义）。配置损坏/装配失败不炸服务——
    // warn 后按 noop 继续（配置是用户资产，坏文件要响亮但服务不能死）。
    let otel_provider = assemble_otel_provider();

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

    // 退出前 flush：把队列里剩的指标推完再走（provider 有界关闭语义）
    if let Some(provider) = otel_provider {
        provider.shutdown();
    }

    Ok(())
}
