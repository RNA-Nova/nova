use anyhow::Context;
use anyhow::Result;
mod aws_auth;
use clap::Parser;
use clap::Subcommand;
use clap::ValueEnum;
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
    #[command(subcommand)]
    command: Option<ExecServerSubcommand>,

    /// 将本执行端注册到 Nova Registry，而非只监听本地传输。
    #[arg(long, value_name = "URL", requires = "environment_id")]
    remote: Option<String>,

    /// Registry 中预先 enrollment 的环境 ID。
    #[arg(long, value_name = "ID", env = "NOVA_EXEC_SERVER_ENVIRONMENT_ID")]
    environment_id: Option<String>,

    /// 远程注册后的承载协议：Noise Relay 或带鉴权的 Direct WebSocket。
    #[arg(long, value_enum, default_value = "noise")]
    remote_transport: RemoteTransportArg,

    /// Nova Registry 的节点 token；也可用环境变量注入。
    #[arg(long, env = "NOVA_EXEC_SERVER_REGISTRY_TOKEN", hide_env_values = true)]
    registry_token: Option<String>,

    /// 从 enrollment JSON 读取 Nova Registry 的节点 token。
    #[arg(long, value_name = "PATH", conflicts_with = "registry_token")]
    registry_enrollment: Option<PathBuf>,

    /// 使用 AWS SigV4 签名 Direct 注册与 WebSocket 握手。
    #[arg(long, requires = "remote")]
    aws_sigv4: bool,
    #[arg(long, requires = "aws_sigv4")]
    aws_profile: Option<String>,
    #[arg(long, requires = "aws_sigv4")]
    aws_region: Option<String>,
    #[arg(long, default_value = "execute-api", requires = "aws_sigv4")]
    aws_service: String,

    /// Transport endpoint URL. Supported values: `ws://IP:PORT` (default), `stdio`/`stdio://`.
    /// （wss 不支持：服务端只做回环承载，TLS 归上层隧道/中继层。
    /// 默认值对位 codex 与 transport.rs 的 DEFAULT_LISTEN_URL——`:0` 临时端口，
    /// 双实例不撞口，实际端口起服后打印在 stdout）
    #[arg(long, value_name = "URL", default_value = nova_exec_server::DEFAULT_LISTEN_URL)]
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
    /// （对位 codex 39f4277087 `exec_server_command.rs` 同名旗标；nova 本地监听与
    /// forward 子命令共用该启动策略）
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
    #[arg(long, env = nova_exec_server::NOVA_EXEC_SERVER_EXIT_ON_STDIN_CLOSE_ENV_VAR)]
    exit_on_stdin_close: bool,
}

#[derive(Clone, Debug, Subcommand)]
enum ExecServerSubcommand {
    /// 将远程 Noise Relay 流转发到一个已运行的回环 exec-server。
    Forward(ForwardArgs),
}

#[derive(Clone, Debug, clap::Args)]
struct ForwardArgs {
    #[arg(long, value_name = "WS_URL")]
    connect: String,
    #[arg(long, value_name = "URL")]
    remote: String,
    #[arg(long, value_name = "ID", env = "NOVA_EXEC_SERVER_ENVIRONMENT_ID")]
    environment_id: String,
    #[arg(long, env = "NOVA_EXEC_SERVER_REGISTRY_TOKEN", hide_env_values = true)]
    registry_token: String,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, ValueEnum)]
enum RemoteTransportArg {
    #[default]
    Noise,
    Direct,
}

enum RemoteAuth {
    NovaToken(String),
    AwsSigV4 {
        profile: Option<String>,
        region: Option<String>,
        service: String,
    },
}

enum RunMode {
    Local,
    Remote {
        base_url: String,
        environment_id: String,
        transport: RemoteTransportArg,
        auth: RemoteAuth,
    },
    Forward(ForwardArgs),
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
/// 注意用 eprintln 而非 tracing（对位 codex build_provider 的错误路径）：
/// 本函数在 install_stderr_logging 之前运行（otel layer 要等 provider 出来
/// 才能挂），此间任何 tracing::warn 都会落进无 subscriber 真空被丢弃。
fn assemble_otel_provider() -> Option<nova_exec_server_otel::OtelProvider> {
    let home = match nova_exec_server_utils_home_dir::find_nova_exec_server_home() {
        Ok(home) => home,
        Err(error) => {
            eprintln!("executor home 解析失败，遥测按静默 noop 继续：{error}");
            return None;
        }
    };
    let settings = match nova_exec_server_otel::load_otel_settings(home.as_path()) {
        Ok(settings) => settings,
        Err(error) => {
            eprintln!("[otel] 配置读取失败，遥测按静默 noop 继续：{error}");
            return None;
        }
    };
    match nova_exec_server_otel::OtelProvider::try_new(&settings) {
        Ok(provider) => provider,
        Err(error) => {
            eprintln!("OTEL provider 装配失败，遥测按静默 noop 继续：{error}");
            None
        }
    }
}

#[cfg(target_os = "macos")]
fn allowed_symlinked_nova_home_from_config()
-> Option<nova_exec_server_utils_absolute_path::AbsolutePathBuf> {
    let home = nova_exec_server_utils_home_dir::find_nova_exec_server_home().ok()?;
    let config_text = std::fs::read_to_string(home.join("config.toml")).ok()?;
    if !allow_symlinked_nova_home_enabled(&config_text) {
        return None;
    }

    // 保留 NOVA_EXEC_SERVER_HOME 配置的别名形式，但其规范化目标必须等于
    // 实际 executor home（对位 codex allowed_symlinked_codex_home）。
    let configured_alias = std::env::var_os("NOVA_EXEC_SERVER_HOME")
        .filter(|path| !path.is_empty())
        .and_then(|path| {
            nova_exec_server_utils_absolute_path::AbsolutePathBuf::from_absolute_path(path).ok()
        })
        .filter(|path| path.canonicalize().ok().as_ref() == Some(&home));
    Some(configured_alias.unwrap_or(home))
}

#[cfg(target_os = "macos")]
fn allow_symlinked_nova_home_enabled(config_text: &str) -> bool {
    config_text
        .parse::<toml::Table>()
        .ok()
        .and_then(|config| config.get("allow_symlinked_nova_home")?.as_bool())
        .unwrap_or(false)
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

#[derive(serde::Deserialize)]
struct RegistryEnrollment {
    node_token: String,
}

fn read_registry_enrollment(path: &std::path::Path) -> Result<String> {
    let contents = std::fs::read(path).with_context(|| {
        format!(
            "failed to read registry enrollment {}",
            path.display()
        )
    })?;
    let enrollment: RegistryEnrollment = serde_json::from_slice(&contents).with_context(|| {
        format!(
            "failed to parse registry enrollment {}",
            path.display()
        )
    })?;
    anyhow::ensure!(
        !enrollment.node_token.trim().is_empty(),
        "registry enrollment {} has an empty node_token",
        path.display()
    );
    Ok(enrollment.node_token)
}

#[tokio::main]
async fn run_server() -> Result<()> {
    let cli = Cli::parse();
    let run_mode = if let Some(ExecServerSubcommand::Forward(args)) = cli.command.clone() {
        if cli.remote.is_some() || cli.aws_sigv4 {
            anyhow::bail!("forward cannot be combined with top-level --remote or --aws-sigv4");
        }
        RunMode::Forward(args)
    } else if let Some(base_url) = cli.remote.clone() {
        let environment_id = cli
            .environment_id
            .clone()
            .context("--remote requires --environment-id")?;
        let auth = if cli.aws_sigv4 {
            if cli.remote_transport != RemoteTransportArg::Direct {
                anyhow::bail!("--aws-sigv4 requires --remote-transport direct");
            }
            RemoteAuth::AwsSigV4 {
                profile: cli.aws_profile.clone(),
                region: cli.aws_region.clone(),
                service: cli.aws_service.clone(),
            }
        } else {
            let token = match cli.registry_enrollment.as_ref() {
                Some(path) => read_registry_enrollment(path)?,
                None => cli.registry_token.clone().context(
                    "--remote requires --registry-token, --registry-enrollment, or NOVA_EXEC_SERVER_REGISTRY_TOKEN",
                )?,
            };
            RemoteAuth::NovaToken(token)
        };
        RunMode::Remote {
            base_url,
            environment_id,
            transport: cli.remote_transport,
            auth,
        }
    } else {
        RunMode::Local
    };

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
    // 对位 codex arg0 `linux_sandbox_exe_path`：helper 路径缺省时建
    // `nova-linux-sandbox` 软链别名（basename 即分派名，无 --argv0 的老
    // bwrap 也能触发）。别名落在 executor home 的受限目录并持锁存活，
    // 不使用会被按龄清理的系统临时目录。
    #[cfg(target_os = "linux")]
    let (executor_linux_sandbox_exe, _alias_guard) = match cli.executor_linux_sandbox_exe {
        Some(path) => (Some(path), None),
        None => {
            let (alias, guard) = create_linux_sandbox_alias(executor_self_exe.as_path())?;
            (Some(alias), Some(guard))
        }
    };
    #[cfg(not(target_os = "linux"))]
    let executor_linux_sandbox_exe = cli.executor_linux_sandbox_exe;
    let runtime_paths = ExecServerRuntimePaths::new(executor_self_exe, executor_linux_sandbox_exe)?
        .with_linux_sandbox_pid_namespace(cli.linux_sandbox_pid_namespace)
        .with_proxy_private_ips_via_upstream(cli.proxy_private_ips_via_upstream);
    #[cfg(target_os = "macos")]
    let runtime_paths =
        runtime_paths.with_allowed_symlinked_nova_home(allowed_symlinked_nova_home_from_config());

    let result = match run_mode {
        RunMode::Local => {
            // 本地模式：WS 回环 / stdio 承载；入站鉴权机制在、默认关。
            let parent_stdin_leash = cli.exit_on_stdin_close && !cli.listen.starts_with("stdio");
            run_until_shutdown(
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
            .await
        }
        RunMode::Remote {
            base_url,
            environment_id,
            transport,
            auth,
        } => {
            let transport = match transport {
                RemoteTransportArg::Noise => nova_exec_server::RemoteEnvironmentTransport::Noise,
                RemoteTransportArg::Direct => nova_exec_server::RemoteEnvironmentTransport::Direct,
            };
            let factory = nova_exec_server_http_client::HttpClientFactory::new(
                nova_exec_server_http_client::OutboundProxyPolicy::ReqwestDefault,
            );
            let auth = match auth {
                RemoteAuth::NovaToken(token) => {
                    nova_exec_server::RegistryBearerAuth::shared(&token)?
                }
                RemoteAuth::AwsSigV4 {
                    profile,
                    region,
                    service,
                } => {
                    aws_auth::aws_sigv4_auth_provider(
                        nova_exec_server_aws_auth::AwsAuthConfig {
                            profile,
                            region,
                            service,
                        },
                        factory.clone(),
                    )
                    .await?
                }
            };
            let mut config = nova_exec_server::RemoteEnvironmentConfig::new_with_transport(
                base_url,
                environment_id,
                transport,
                auth,
                factory,
            )?
            .with_telemetry(telemetry);
            config.request_dispatch_mode = cli.request_dispatch_mode;
            run_until_shutdown(
                async move {
                    nova_exec_server::run_remote_environment_until_shutdown(
                        config,
                        runtime_paths,
                        std::future::pending(),
                    )
                    .await
                    .map_err(
                        |error| -> Box<dyn std::error::Error + Send + Sync> { Box::new(error) },
                    )
                },
                cli.exit_on_stdin_close,
            )
            .await
        }
        RunMode::Forward(args) => {
            let auth = nova_exec_server::RegistryBearerAuth::shared(&args.registry_token)?;
            let config = nova_exec_server::RemoteEnvironmentConfig::new(
                args.remote,
                args.environment_id,
                auth,
                nova_exec_server_http_client::HttpClientFactory::new(
                    nova_exec_server_http_client::OutboundProxyPolicy::ReqwestDefault,
                ),
            )?
            .with_telemetry(telemetry);
            run_until_shutdown(
                async move {
                    nova_exec_server::run_remote_environment_forward_until_shutdown(
                        config,
                        args.connect,
                        std::future::pending(),
                    )
                    .await
                    .map_err(
                        |error| -> Box<dyn std::error::Error + Send + Sync> { Box::new(error) },
                    )
                },
                cli.exit_on_stdin_close,
            )
            .await
        }
    };

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

/// 别名目录与锁文件的存活守卫：目录随进程退出清理，锁随文件关闭释放。
#[cfg(target_os = "linux")]
struct LinuxSandboxAliasGuard {
    _temp_dir: tempfile::TempDir,
    _lock_file: std::fs::File,
}

/// 建主二进制的 `nova-linux-sandbox` 软链别名（对位 codex arg0 的别名段）。
///
/// 目录固定为 `<executor home>/tmp/arg0/<每进程目录>`，权限 0700；释放前
/// 先清理未被其他进程持有的陈旧目录。release 下拒绝把别名放进系统临时
/// 目录——该位置会被按龄清理，长命服务的别名可能中途消失。
#[cfg(target_os = "linux")]
fn create_linux_sandbox_alias(
    self_exe: &std::path::Path,
) -> std::io::Result<(std::path::PathBuf, LinuxSandboxAliasGuard)> {
    use std::os::unix::fs::PermissionsExt;

    let executor_home = nova_exec_server_utils_home_dir::find_nova_exec_server_home()?;
    #[cfg(not(debug_assertions))]
    {
        let temp_root = std::env::temp_dir();
        if executor_home.as_path().starts_with(&temp_root) {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                format!(
                    "refusing to create Linux sandbox alias under temporary dir {} (executor home: {})",
                    temp_root.display(),
                    executor_home.display()
                ),
            ));
        }
    }

    let temp_root = executor_home.join("tmp").join("arg0");
    std::fs::create_dir_all(&temp_root)?;
    std::fs::set_permissions(&temp_root, std::fs::Permissions::from_mode(0o700))?;
    if let Err(error) = janitor_cleanup(temp_root.as_path()) {
        eprintln!("warning: failed to clean up stale Linux sandbox alias dirs: {error}");
    }

    let temp_dir = tempfile::Builder::new()
        .prefix("nova-exec-server-arg0")
        .tempdir_in(&temp_root)?;
    let lock_file = std::fs::File::options()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(temp_dir.path().join(".lock"))?;
    lock_file_exclusive(&lock_file)?;

    let alias = temp_dir
        .path()
        .join(nova_exec_server_sandboxing::landlock::NOVA_EXEC_SERVER_LINUX_SANDBOX_ARG0);
    std::os::unix::fs::symlink(self_exe, &alias)?;
    Ok((
        alias,
        LinuxSandboxAliasGuard {
            _temp_dir: temp_dir,
            _lock_file: lock_file,
        },
    ))
}

/// 清理未被其他进程持有的陈旧别名目录（对位 codex arg0 的 janitor）。
#[cfg(target_os = "linux")]
fn janitor_cleanup(temp_root: &std::path::Path) -> std::io::Result<()> {
    let entries = match std::fs::read_dir(temp_root) {
        Ok(entries) => entries,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error),
    };

    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }
        let lock_path = path.join(".lock");
        let lock_file = match std::fs::File::options()
            .read(true)
            .write(true)
            .open(lock_path)
        {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(error) => return Err(error),
        };
        if lock_file_exclusive(&lock_file).is_err() {
            // 锁仍被活进程持有：目录不能删；文件关闭即释放测试锁。
            continue;
        }
        match std::fs::remove_dir_all(&path) {
            Ok(()) => {}
            // 允许 read_dir 与删除之间的正常 TOCTOU 竞态。
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(error) => return Err(error),
        }
    }
    Ok(())
}

#[cfg(target_os = "linux")]
fn lock_file_exclusive(file: &std::fs::File) -> std::io::Result<()> {
    use std::os::unix::io::AsRawFd;

    if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } < 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(())
}

#[cfg(test)]
mod registry_enrollment_tests {
    use super::Cli;
    use super::read_registry_enrollment;
    use clap::Parser;
    use clap::error::ErrorKind;

    #[test]
    fn reads_node_token_without_exposing_it_in_errors() {
        let root = tempfile::tempdir().expect("temp root");
        let path = root.path().join("enrollment.json");
        std::fs::write(&path, r#"{"node_token":"test-node-token","other":true}"#)
            .expect("write enrollment");
        assert_eq!(
            read_registry_enrollment(&path).expect("read enrollment"),
            "test-node-token"
        );

        std::fs::write(&path, r#"{"node_token":"  "}"#).expect("write empty enrollment");
        let error = read_registry_enrollment(&path).expect_err("reject empty token");
        assert!(error.to_string().contains("empty node_token"));
        assert!(!error.to_string().contains("test-node-token"));
    }

    #[test]
    fn enrollment_conflicts_with_direct_token() {
        let error = Cli::try_parse_from([
            "nova-exec-server",
            "--remote",
            "https://relay.example",
            "--environment-id",
            "windows-dev",
            "--registry-token",
            "test-token",
            "--registry-enrollment",
            "enrollment.json",
        ])
        .expect_err("reject conflicting token sources");
        assert_eq!(error.kind(), ErrorKind::ArgumentConflict);
    }
}

#[cfg(all(test, target_os = "linux"))]
mod linux_alias_tests {
    use super::janitor_cleanup;
    use super::lock_file_exclusive;
    use std::fs;

    #[test]
    fn janitor_removes_only_unlocked_alias_directories() {
        let root = tempfile::tempdir().expect("temp root");
        let unlocked = root.path().join("unlocked");
        let locked = root.path().join("locked");
        fs::create_dir_all(&unlocked).expect("create unlocked dir");
        fs::create_dir_all(&locked).expect("create locked dir");
        fs::write(unlocked.join(".lock"), b"").expect("write unlocked lock");
        fs::write(locked.join(".lock"), b"").expect("write locked lock");

        let held = fs::File::options()
            .read(true)
            .write(true)
            .open(locked.join(".lock"))
            .expect("open held lock");
        lock_file_exclusive(&held).expect("hold alias lock");

        janitor_cleanup(root.path()).expect("janitor cleanup");
        assert!(!unlocked.exists(), "unlocked directory must be removed");
        assert!(locked.exists(), "locked directory must survive");

        drop(held);
        janitor_cleanup(root.path()).expect("cleanup after release");
        assert!(!locked.exists(), "released directory must be removed");
    }
}

#[cfg(all(test, target_os = "macos"))]
mod symlink_config_tests {
    use super::allow_symlinked_nova_home_enabled;

    #[test]
    fn parses_symlink_opt_out_flag() {
        assert!(allow_symlinked_nova_home_enabled(
            "allow_symlinked_nova_home = true\n"
        ));
        assert!(!allow_symlinked_nova_home_enabled(
            "allow_symlinked_nova_home = false\n"
        ));
        assert!(!allow_symlinked_nova_home_enabled(
            "allow_symlinked_nova_home = \"yes\"\n"
        ));
        assert!(!allow_symlinked_nova_home_enabled("[otel]\n"));
        assert!(!allow_symlinked_nova_home_enabled("not valid toml"));
    }
}
