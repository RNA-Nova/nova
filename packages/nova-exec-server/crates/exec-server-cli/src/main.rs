use anyhow::Context;
use anyhow::Result;
use clap::Parser;
use std::path::PathBuf;

use nova_exec_server::ExecServerRuntimePaths;
use nova_exec_server::RequestDispatchMode;
use nova_exec_server::run_main_with_telemetry;
use nova_exec_server_websocket_auth::WebsocketAuthArgs;

/// otel 服务名（对位 codex OTEL_SERVICE_NAME="codex-exec-server"）：
/// 进程启动计数的 originator 标签值。
const OTEL_SERVICE_NAME: &str = "nova-exec-server";

/// nova-exec-server - 远程执行服务
#[derive(Debug, Parser)]
#[command(name = "nova-exec-server")]
#[command(about = "Nova remote executor service")]
struct Cli {
    /// Transport endpoint URL. Supported values: `ws://IP:PORT` (default), `stdio`/`stdio://`.
    /// （wss 不支持：服务端只做回环承载，TLS 归上层隧道/中继层）
    #[arg(long, value_name = "URL", default_value = "ws://127.0.0.1:8080")]
    listen: String,

    /// WebSocket 入站鉴权旗标组（对位 codex 39f4277087
    /// `exec_server_command.rs:78` 的 `#[command(flatten)] WebsocketAuthArgs`；
    /// 默认关——不传 `--ws-auth` 时保持无鉴权现状）。
    #[command(flatten)]
    websocket_auth: WebsocketAuthArgs,

    /// Maximum number of requests to process concurrently on each connection.
    /// 默认 1（Inline 串行，与 codex 对齐）：把跨请求竞态从结构上消灭——
    /// 服务端共享状态不再被并发摸，无需维护"先查后做"审计纪律；
    /// 长命令/流传输本就后台异步执行不受影响（实测薄 handler 下与并发无差）。
    /// 显式调大仅用于"慢 handler + 并行调用"场景（如大 fs/readFile 整读时
    /// 不堵整轮——实测该组合下并发快 150x）；并发模式的安全由句柄表
    /// per-id 在飞预约等防线承载。
    /// （对位 codex exec_server_command.rs：旗标名 `--concurrent-requests`，
    /// 直接以 RequestDispatchMode 的 FromStr 解析——0 经 NonZeroUsize 报错、
    /// 超 Semaphore::MAX_PERMITS 钳到上限）。
    #[arg(
        long = "concurrent-requests",
        value_name = "COUNT",
        default_value = "1"
    )]
    request_dispatch_mode: RequestDispatchMode,

    /// Path to the executor executable used to launch hidden helper modes.
    #[arg(long, value_name = "PATH")]
    executor_self_exe: Option<PathBuf>,

    /// Path to the Linux sandbox helper alias.
    #[arg(long, value_name = "PATH")]
    executor_linux_sandbox_exe: Option<PathBuf>,

    /// Linux PID namespace: isolate (default) or inherit. Inherit allows signals to
    /// other same-UID processes; enable only when provisioning a dedicated environment.
    /// （对位 codex 39f4277087 `exec_server_command.rs` 同名旗标；nova CLI 无
    /// subcommand，故不带 codex 的 global = true）
    #[arg(long, value_name = "MODE", default_value = "isolate")]
    linux_sandbox_pid_namespace: nova_exec_server_sandboxing::LinuxSandboxPidNamespace,

    /// Allow permitted private IP destinations to use the configured upstream proxy.
    /// If no valid upstream proxy applies to the request protocol, connect directly.
    /// Loopback stays local. This flag does not require upstream routing.
    /// （对位 codex 39f4277087 `exec_server_command.rs` 同名旗标）
    #[arg(long, env = "NOVA_EXEC_SERVER_PROXY_PRIVATE_IPS_VIA_UPSTREAM")]
    proxy_private_ips_via_upstream: bool,

    /// 父死子随：WS 托管 spawn 场景下，父进程持有的 stdin 管道关闭即退出
    /// （对位 codex --exit-on-stdin-close / ParentLifetime::StdinPipe）。
    /// stdio 形态下 stdin 本就是传输线，EOF 自然结束服务，此旗标为 no-op。
    #[arg(long, env = "NOVA_EXEC_SERVER_EXIT_ON_STDIN_CLOSE")]
    exit_on_stdin_close: bool,
}

/// 安装 stderr 日志 + otel 输出 layer（对位 codex exec_server_telemetry::init
/// 的 subscriber 装配：fmt layer + provider 的 tracing/logger layer）——此前
/// 服务端全部 tracing 日志在生产被静默丢弃（无任何 subscriber），这是补齐
/// 而非新特性。otel layer 仅在 provider 装配成功时挂上。
fn install_stderr_logging(otel_provider: Option<&nova_exec_server_otel::OtelProvider>) {
    use tracing_subscriber::prelude::*;
    let filter = tracing_subscriber::EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("warn"));
    let fmt_layer = tracing_subscriber::fmt::layer()
        .with_writer(std::io::stderr)
        .with_filter(filter);
    let otel_logger_layer = otel_provider.and_then(|provider| provider.logger_layer());
    let otel_tracing_layer = otel_provider.and_then(|provider| provider.tracing_layer());
    let _ = tracing_subscriber::registry()
        .with(fmt_layer)
        .with(otel_tracing_layer)
        .with(otel_logger_layer)
        .try_init(); // 重复 init（测试嵌入场景）不报错
    // fmt/otel layer 后注册后重建缓存——此前按无 subscriber 静态判定为
    // disabled 的 callsite 需要重新求值（对位 codex init 末尾）。
    tracing::callsite::rebuild_interest_cache();
}

/// 关闭信号监听（对位 codex exec_server_telemetry.rs 的 ShutdownSignal 族）：
/// unix = SIGINT + SIGTERM，其他平台 = SIGINT（Ctrl-C）。
#[cfg(unix)]
struct ShutdownSignal {
    terminate: tokio::signal::unix::Signal,
}

#[cfg(unix)]
fn shutdown_signal() -> std::io::Result<ShutdownSignal> {
    Ok(ShutdownSignal {
        terminate: tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?,
    })
}

#[cfg(unix)]
async fn wait_for_shutdown_signal(mut shutdown_signal: ShutdownSignal) -> std::io::Result<()> {
    tokio::select! {
        result = tokio::signal::ctrl_c() => result,
        _ = shutdown_signal.terminate.recv() => Ok(()),
    }
}

#[cfg(not(unix))]
struct ShutdownSignal;

#[cfg(not(unix))]
fn shutdown_signal() -> std::io::Result<ShutdownSignal> {
    Ok(ShutdownSignal)
}

#[cfg(not(unix))]
async fn wait_for_shutdown_signal(_: ShutdownSignal) -> std::io::Result<()> {
    tokio::signal::ctrl_c().await
}

/// 服务运行至关闭（对位 codex exec_server_telemetry::run_until_shutdown；
/// 锚点本地路径同为 Immediate——不等排空，信号命中即返回 Ok）：服务 future
/// 与 关闭信号 / 父 stdin 关闭（WS 托管 leash）三者竞争。返回后由调用方做
/// 遥测 flush——此前 SIGINT/SIGTERM 走默认 disposition（exit 130/143），
/// flush 永远跳过。
async fn run_until_shutdown(
    run: impl std::future::Future<Output = Result<(), Box<dyn std::error::Error + Send + Sync>>>,
    parent_stdin_leash: bool,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    // 父死子随：父进程持有的 stdin 管道 EOF 即关闭信号（对位 codex 的
    // io::copy(stdin→sink) 完成语义——copy 返回时父已死）
    let parent_disconnected = if parent_stdin_leash {
        let (sender, receiver) = tokio::sync::oneshot::channel();
        std::thread::spawn(move || {
            if let Err(error) = std::io::copy(&mut std::io::stdin().lock(), &mut std::io::sink()) {
                tracing::warn!(%error, "Could not read exec-server parent lifetime pipe");
            }
            let _ = sender.send(());
        });
        Some(receiver)
    } else {
        None
    };
    let parent_disconnected = async move {
        match parent_disconnected {
            Some(receiver) => {
                let _ = receiver.await;
            }
            None => std::future::pending().await,
        }
    };
    let shutdown_signal = match shutdown_signal() {
        Ok(signal) => Some(signal),
        Err(error) => {
            eprintln!("Could not listen for exec-server shutdown signal: {error}");
            None
        }
    };
    let shutdown_signal = async move {
        match shutdown_signal {
            Some(signal) => wait_for_shutdown_signal(signal).await,
            None => std::future::pending().await,
        }
    };

    tokio::pin!(run, parent_disconnected, shutdown_signal);
    let mut signal_enabled = true;
    loop {
        tokio::select! {
            result = &mut run => return result,
            _ = &mut parent_disconnected => {
                tracing::info!("Stopping exec-server after its parent closed stdin");
                break;
            }
            signal = &mut shutdown_signal, if signal_enabled => {
                match signal {
                    Ok(()) => break,
                    Err(error) => {
                        eprintln!("Could not listen for exec-server shutdown signal: {error}");
                        signal_enabled = false;
                    }
                }
            }
        }
    }
    Ok(())
}

/// 遥测装配（codex build_provider 对位）：读 executor 配置根 config.toml 的
/// [otel] 段 → 装全局 provider。无配置/无出口 = None（静默 noop）；
/// 配置损坏/装配失败 = warn 后 None（响亮但不炸服务）。
fn assemble_otel_provider() -> Option<nova_exec_server_otel::OtelProvider> {
    let home = match nova_exec_server_utils_home_dir::find_nova_exec_server_home() {
        Ok(home) => home,
        Err(error) => {
            tracing::warn!(%error, "executor home 解析失败，遥测按静默 noop 继续");
            return None;
        }
    };
    let settings = match nova_exec_server_otel::load_otel_settings(home.as_path()) {
        Ok(settings) => settings,
        Err(error) => {
            tracing::warn!(%error, "[otel] 配置读取失败，遥测按静默 noop 继续");
            return None;
        }
    };
    match nova_exec_server_otel::OtelProvider::try_new(&settings) {
        Ok(provider) => provider,
        Err(error) => {
            tracing::warn!(%error, "OTEL provider 装配失败，遥测按静默 noop 继续");
            None
        }
    }
}

fn main() -> Result<()> {
    // 对位 codex arg0/src/lib.rs:61：启动早期注册/分派 spawn setup helper——
    // Linux 下 pipe/PTY spawn 的原生启动（免 fork 主进程）依赖它；argv1 为
    // "--nova-run-as-process-setup" 时由 init_spawn_helper 内部直接分派执行
    // setup 并退出（不会走到下面的 clap 分派）。
    #[cfg(target_os = "linux")]
    nova_exec_server_utils_pty::init_spawn_helper(std::env::args_os());

    // 对位 codex arg0_dispatch（arg0/src/lib.rs:61-72）：argv0 basename 分派
    // 先于 argv1 隐藏旗标——以 "nova-linux-sandbox" 之名启动（软链别名自分身
    // 或 bwrap --argv0 注入）时直接执行沙箱 helper 并退出，主二进制自足。
    #[cfg(target_os = "linux")]
    {
        let mut dispatch_args = std::env::args_os();
        let argv0 = dispatch_args.next().unwrap_or_default();
        let exe_name = std::path::Path::new(&argv0)
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("")
            .to_owned();
        if exe_name == nova_exec_server_sandboxing::landlock::NOVA_EXEC_SERVER_LINUX_SANDBOX_ARG0 {
            nova_exec_server_linux_sandbox::run_main();
        }
    }

    // 隐藏 helper 模式先于 clap 与 tokio 运行时分派：沙箱化 fs 操作与 arg0
    // 执行辅助分别以隐藏 flag 重启本二进制，helper 自建运行时并直接退出
    //（不能在已有 runtime 的 block_on 里再进 block_on）。
    // 注意先取出第二参数再做匹配：args.next() 会消费参数，边取边比会丢参。
    let mut args = std::env::args_os();
    let _program = args.next();
    match args.next().as_deref() {
        // MXC helper 入口对位 codex 42cd1ec497：先取 argv1 再分派，helper
        // 解包环境传输后直接以子进程退出码退出。
        Some(arg)
            if arg
                == std::ffi::OsStr::new(
                    nova_exec_server_sandboxing::NOVA_EXEC_SERVER_WINDOWS_MXC_ARG1,
                ) =>
        {
            nova_exec_server_sandboxing::run_windows_mxc_main();
        }
        Some(arg)
            if arg == std::ffi::OsStr::new(nova_exec_server::NOVA_EXEC_SERVER_FS_HELPER_ARG1) =>
        {
            nova_exec_server::run_fs_helper_main();
        }
        Some(arg)
            if arg
                == std::ffi::OsStr::new(
                    nova_exec_server::NOVA_EXEC_SERVER_ARG0_EXEC_HELPER_ARG1,
                ) =>
        {
            nova_exec_server::run_arg0_exec_helper_main();
        }
        // Windows 沙箱包装器入口（对位 codex arg0/src/lib.rs:85-88）：沙箱化
        // 进程/fs helper 经 `<self> --run-as-windows-sandbox` 重入本二进制。
        #[cfg(target_os = "windows")]
        Some(arg)
            if arg
                == std::ffi::OsStr::new(
                    nova_exec_server_windows_sandbox::NOVA_EXEC_SERVER_WINDOWS_SANDBOX_ARG1,
                ) =>
        {
            nova_exec_server_windows_sandbox::run_windows_sandbox_wrapper_main();
        }
        _ => {}
    }
    run_server()
}

#[tokio::main]
async fn run_server() -> Result<()> {
    let cli = Cli::parse();

    // 对位 codex 39f4277087 exec_server_command.rs：鉴权旗标组先折叠成
    // settings（组合非法在此即报错）；默认 None = 无鉴权，保持现状。
    let websocket_auth = cli.websocket_auth.try_into_settings()?;

    // 遥测装配：executor 配置根 config.toml 的 [otel] 段配了 metrics 出口则
    // 装全局 provider（对位 codex build_provider 的配置→装配形状）；
    // 没配 = 全静默 noop（零外发，可选语义）。配置损坏/装配失败不炸服务——
    // warn 后按 noop 继续（配置是用户资产，坏文件要响亮但服务不能死）。
    let otel_provider = assemble_otel_provider();

    // stderr 日志 + otel 输出 layer（provider 装配后安装，layer 才能挂上）
    install_stderr_logging(otel_provider.as_ref());

    // 进程启动计数（对位 codex record_process_start；无 provider 即 noop）
    if let Some(metrics) = otel_provider
        .as_ref()
        .and_then(|provider| provider.metrics())
    {
        let _ = nova_exec_server_otel::record_process_start_once(metrics, OTEL_SERVICE_NAME);
    }
    // 对位 codex exec_server_telemetry::init：provider 的 metrics 接到服务端
    // telemetry——此前装好了 provider 却传 ExecServerTelemetry::default()
    //（inner=None），配了 [otel] 服务端指标也全静默丢失。
    let telemetry = otel_provider
        .as_ref()
        .and_then(|provider| provider.metrics())
        .cloned()
        .map(nova_exec_server::ExecServerTelemetry::new)
        .unwrap_or_default();

    // 构建 runtime paths
    let executor_self_exe = cli
        .executor_self_exe
        .or_else(|| std::env::current_exe().ok())
        .context("executor self executable path is not configured")?;
    // 对位 codex arg0 `linux_sandbox_exe_path`：helper 路径缺省时自我回退——
    // 优先每进程临时目录里的软链别名（basename 即分派名，无 --argv0 的老
    // bwrap 也能触发）；别名建不了退化为二进制本体（现代 bwrap 经 --argv0
    // 注入 magic 名照常分派）。裁点：codex 的别名目录带跨进程锁，我们收窄
    // 为进程私有 TempDir（无跨进程共享面）。
    #[cfg(target_os = "linux")]
    let (executor_linux_sandbox_exe, _alias_guard) = match cli.executor_linux_sandbox_exe {
        Some(path) => (Some(path), None),
        None => match create_linux_sandbox_alias(executor_self_exe.as_path()) {
            Some((alias, guard)) => (Some(alias), Some(guard)),
            None => (Some(executor_self_exe.clone()), None),
        },
    };
    #[cfg(not(target_os = "linux"))]
    let executor_linux_sandbox_exe = cli.executor_linux_sandbox_exe;
    let runtime_paths = ExecServerRuntimePaths::new(executor_self_exe, executor_linux_sandbox_exe)?
        .with_linux_sandbox_pid_namespace(cli.linux_sandbox_pid_namespace)
        .with_proxy_private_ips_via_upstream(cli.proxy_private_ips_via_upstream);

    // 本地模式：WS 回环 / stdio 承载；入站鉴权机制在、默认关
    // （--ws-auth 不传即无鉴权；stdio 形态下配鉴权会被传输层拒绝）。
    // 父死子随 leash 仅 WS 托管场景挂（stdio 形态 stdin 是传输线，EOF 已由
    // 传输层自然结束服务）；SIGINT/SIGTERM 经 run_until_shutdown 优雅返回。
    let parent_stdin_leash = cli.exit_on_stdin_close && !cli.listen.starts_with("stdio");
    let result = run_until_shutdown(
        run_main_with_telemetry(
            &cli.listen,
            runtime_paths,
            telemetry,
            nova_exec_server_http_client::HttpClientFactory::new(
                nova_exec_server_http_client::OutboundProxyPolicy::ReqwestDefault,
            ),
            cli.request_dispatch_mode,
            websocket_auth,
        ),
        parent_stdin_leash,
    )
    .await;

    // 退出前 flush：把队列里剩的指标推完再走（provider 有界关闭语义）——
    // 信号/父死路径也能走到这里（此前信号走默认 disposition，flush 永远跳过）
    if let Some(provider) = otel_provider {
        provider.shutdown();
    }

    // from_boxed 保错误链（对位 codex exec_server_command.rs 末尾；
    // 此前 anyhow!("{err}") 字符串化丢链）
    result.map_err(anyhow::Error::from_boxed)?;

    Ok(())
}

/// 建主二进制的 `nova-linux-sandbox` 软链别名（每进程临时目录，随进程退出
/// 自动清理）。对位 codex arg0 crate 的别名段；其跨进程锁文件收窄掉——
/// 进程私有目录无共享面。
#[cfg(target_os = "linux")]
fn create_linux_sandbox_alias(
    self_exe: &std::path::Path,
) -> Option<(std::path::PathBuf, tempfile::TempDir)> {
    let guard = tempfile::tempdir().ok()?;
    let alias = guard
        .path()
        .join(nova_exec_server_sandboxing::landlock::NOVA_EXEC_SERVER_LINUX_SANDBOX_ARG0);
    std::os::unix::fs::symlink(self_exe, &alias).ok()?;
    Some((alias, guard))
}
