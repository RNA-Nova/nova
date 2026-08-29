//! 集成测试共享夹具：测试二进制经 ctor 兼任 nova-executor 服务器与隐藏 helper。
//!
//! 对位 codex `exec-server/tests/common/mod.rs`，差异点：
//! - 不引入 `codex-test-binary-support` / `codex-arg0` 的 PATH alias 机械
//!   （那是 Bazel 下跨 crate 复用测试二进制的产物）；cargo 下按 argv1 哨兵
//!   分派即可。唯一的 argv0 分派是 Linux 的 `codex-linux-sandbox`：landlock/
//!   bwrap 沙箱会以该 argv0 重入本二进制，须在此直接转给 linux sandbox 入口，
//!   否则 `--sandbox-policy-cwd` 等参数会落进 libtest 的解析器（exit 101）。
//! - 测试隔离的 home 目录不由 ctor 设置，改为夹具按子进程注入
//!   `NOVA_EXECUTOR_HOME`（见 `exec_server.rs`）。

use std::env;
use std::io::Write;
use std::path::Path;
use std::path::PathBuf;
use std::process::Command;
use std::process::Stdio;
use std::time::Duration;

use ctor::ctor;
use nova_executor_http_client::HttpClientFactory;
use nova_executor_http_client::OutboundProxyPolicy;
use nova_executor_server::CODEX_ARG0_EXEC_HELPER_ARG1;
use nova_executor_server::CODEX_FS_HELPER_ARG1;
use nova_executor_server::ExecServerRuntimePaths;
use nova_executor_server::ExecServerTelemetry;
use nova_executor_server::RequestDispatchMode;

pub(crate) mod exec_server;
// relay 测试专用的假 rendezvous/registry 原语（对位 codex test-support crate
// 的 relay 模块，nova 折叠进测试 common；仅 relay 系列测试使用）。
#[allow(dead_code)]
pub(crate) mod test_support_relay;

pub(crate) const DELAYED_OUTPUT_AFTER_EXIT_PARENT_ARG: &str =
    "--nova-test-delayed-output-after-exit-parent";
pub(crate) const SYSTEM_PROXY_REQUEST_URL_ENV: &str = "NOVA_EXECUTOR_TEST_SYSTEM_PROXY_REQUEST_URL";
pub(crate) const SYSTEM_PROXY_URL_ENV: &str = "NOVA_EXECUTOR_TEST_SYSTEM_PROXY_URL";

const DELAYED_OUTPUT_AFTER_EXIT_CHILD_ARG: &str = "--nova-test-delayed-output-after-exit-child";
const EXEC_SERVER_SUBCOMMAND: &str = "exec-server";
#[cfg(target_os = "windows")]
const NOVA_WINDOWS_SANDBOX_ARG1: &str = "--run-as-windows-sandbox";

/// 测试进程启动时的隐藏入口分派。
///
/// 沙箱化进程 / fs 操作会以隐藏 argv1 重启本二进制；`exec-server` 子命令则
/// 让夹具把测试二进制当服务器用（`ExecServerHarness` spawn 的就是它）。
/// 这些入口全部直接退出进程，绝不返回到测试 main。
#[ctor]
static TEST_BINARY_DISPATCH: () = {
    let mut args = env::args_os();
    let program = args.next();

    // Linux：landlock/bwrap 沙箱把 fs helper 命令改写为以 argv0
    // `codex-linux-sandbox` 重入本二进制（参数形如 `--sandbox-policy-cwd ...`）。
    // 这里按 argv0 basename 转给真正的 linux sandbox 入口（run_main 不返回）。
    #[cfg(target_os = "linux")]
    if let Some(program) = program.as_deref() {
        let is_linux_sandbox = Path::new(program).file_name().is_some_and(|name| {
            name == nova_executor_sandboxing::landlock::CODEX_LINUX_SANDBOX_ARG0
        });
        if is_linux_sandbox {
            // run_main 的 panic（如 bwrap 缺失）若穿出 ctor 的 extern "C" 边界
            // 会变成无诊断信息的 SIGABRT；拦下换成可读错误再退出
            if std::panic::catch_unwind(nova_executor_linux_sandbox::run_main).is_err() {
                eprintln!("codex-linux-sandbox panicked (see panic message above)");
                std::process::exit(101);
            }
            // run_main 正常路径不返回（内部 exec/exit）；走到这里说明实现变了
            eprintln!("codex-linux-sandbox run_main returned unexpectedly");
            std::process::exit(101);
        }
    }
    #[cfg(not(target_os = "linux"))]
    let _ = program;

    let Some(argv1) = args.next() else {
        return;
    };

    if argv1 == CODEX_ARG0_EXEC_HELPER_ARG1 {
        nova_executor_server::run_arg0_exec_helper_main();
    }
    if argv1 == CODEX_FS_HELPER_ARG1 {
        nova_executor_server::run_fs_helper_main();
    }
    #[cfg(target_os = "windows")]
    if argv1 == NOVA_WINDOWS_SANDBOX_ARG1 {
        nova_executor_windows_sandbox::run_windows_sandbox_wrapper_main();
    }

    let Some(command) = argv1.to_str() else {
        return;
    };
    match command {
        DELAYED_OUTPUT_AFTER_EXIT_PARENT_ARG => {
            let release_path = next_release_path_arg(args);
            run_delayed_output_after_exit_parent(&release_path);
        }
        DELAYED_OUTPUT_AFTER_EXIT_CHILD_ARG => {
            let release_path = next_release_path_arg(args);
            run_delayed_output_after_exit_child(&release_path);
        }
        EXEC_SERVER_SUBCOMMAND => run_exec_server_from_test_binary(args),
        _ => {}
    }
};

/// 供测试取“可重入 helper 模式”的二进制路径（即当前测试二进制自身）。
pub(crate) fn current_test_binary_helper_paths() -> anyhow::Result<(PathBuf, Option<PathBuf>)> {
    let current_exe = env::current_exe()?;
    let executor_linux_sandbox_exe = if cfg!(target_os = "linux") {
        Some(current_exe.clone())
    } else {
        None
    };
    Ok((current_exe, executor_linux_sandbox_exe))
}

/// 构建一个不带任何环境的 `EnvironmentManager`（对位 codex test-support crate
/// 的 `environment_manager_without_environments`，nova 折叠进测试 common）。
#[allow(dead_code)]
pub(crate) fn environment_manager_without_environments() -> nova_executor_server::EnvironmentManager
{
    nova_executor_server::EnvironmentManager::without_environments(HttpClientFactory::new(
        OutboundProxyPolicy::ReqwestDefault,
    ))
}

fn next_release_path_arg(mut args: impl Iterator<Item = std::ffi::OsString>) -> PathBuf {
    let Some(release_path) = args.next() else {
        eprintln!("expected release path");
        std::process::exit(1);
    };
    if args.next().is_some() {
        eprintln!("unexpected extra arguments");
        std::process::exit(1);
    }
    PathBuf::from(release_path)
}

/// “父进程已退出但管道仍未关”场景的测试 helper：父进程立即退出，
/// 子进程等 release 文件出现后补写一行输出（见 exec_process.rs 对应用例）。
fn run_delayed_output_after_exit_parent(release_path: &Path) {
    let current_exe = match env::current_exe() {
        Ok(current_exe) => current_exe,
        Err(error) => {
            eprintln!("failed to resolve current test binary: {error}");
            std::process::exit(1);
        }
    };
    match Command::new(current_exe)
        .arg(DELAYED_OUTPUT_AFTER_EXIT_CHILD_ARG)
        .arg(release_path)
        .stdin(Stdio::null())
        .spawn()
    {
        Ok(_) => std::process::exit(0),
        Err(error) => {
            eprintln!("failed to spawn delayed output child: {error}");
            std::process::exit(1);
        }
    }
}

fn run_delayed_output_after_exit_child(release_path: &Path) {
    for _ in 0..1_000 {
        if release_path.exists() {
            let mut stdout = std::io::stdout().lock();
            if let Err(error) = writeln!(stdout, "late output after exit") {
                eprintln!("failed to write delayed output: {error}");
                std::process::exit(1);
            }
            if let Err(error) = stdout.flush() {
                eprintln!("failed to flush delayed output: {error}");
                std::process::exit(1);
            }
            std::process::exit(0);
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    eprintln!(
        "timed out waiting for release path {}",
        release_path.display()
    );
    std::process::exit(1);
}

/// 把测试二进制当 exec-server 跑：`exec-server --listen <url> [--concurrent-requests N]`。
/// 与 `nova-executor` CLI 的本地模式等价，但 runtime paths 指向测试二进制，
/// 使沙箱 helper 重进时命中上面的 ctor 分派。
fn run_exec_server_from_test_binary(mut args: impl Iterator<Item = std::ffi::OsString>) -> ! {
    let Some(flag) = args.next() else {
        eprintln!("expected --listen");
        std::process::exit(1);
    };
    if flag != "--listen" {
        eprintln!("expected --listen, got `{flag:?}`");
        std::process::exit(1);
    }
    let Some(listen_url) = args.next() else {
        eprintln!("expected listen URL");
        std::process::exit(1);
    };
    let remaining_args: Vec<_> = args.collect();
    let request_dispatch_mode = match remaining_args.as_slice() {
        [] => RequestDispatchMode::Inline,
        [flag, value] if flag == "--concurrent-requests" => {
            match value.to_str().map(str::parse::<RequestDispatchMode>) {
                Some(Ok(mode)) => mode,
                Some(Err(error)) => {
                    eprintln!("invalid concurrent request count: {error}");
                    std::process::exit(1);
                }
                None => {
                    eprintln!("invalid concurrent request count");
                    std::process::exit(1);
                }
            }
        }
        args => {
            eprintln!("unexpected exec-server arguments: {args:?}");
            std::process::exit(1);
        }
    };

    let current_exe = match env::current_exe() {
        Ok(current_exe) => current_exe,
        Err(error) => {
            eprintln!("failed to resolve current test binary: {error}");
            std::process::exit(1);
        }
    };
    let linux_sandbox_exe = if cfg!(target_os = "linux") {
        Some(current_exe.clone())
    } else {
        None
    };
    let runtime_paths = match ExecServerRuntimePaths::new(current_exe, linux_sandbox_exe) {
        Ok(runtime_paths) => runtime_paths,
        Err(error) => {
            eprintln!("failed to configure exec-server runtime paths: {error}");
            std::process::exit(1);
        }
    };
    let runtime = match tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
    {
        Ok(runtime) => runtime,
        Err(error) => {
            eprintln!("failed to build Tokio runtime: {error}");
            std::process::exit(1);
        }
    };
    // 系统代理测试通过一对环境变量把代理路由注入子进程（见 http_request.rs）。
    let http_client_factory = match (
        env::var(SYSTEM_PROXY_REQUEST_URL_ENV),
        env::var(SYSTEM_PROXY_URL_ENV),
    ) {
        (Ok(request_url), Ok(proxy_url)) => {
            nova_executor_http_client::cache_system_proxy_route_for_test(&request_url, proxy_url);
            HttpClientFactory::new(OutboundProxyPolicy::RespectSystemProxy)
        }
        (Err(env::VarError::NotPresent), Err(env::VarError::NotPresent)) => {
            HttpClientFactory::new(OutboundProxyPolicy::ReqwestDefault)
        }
        _ => {
            eprintln!("system proxy test configuration requires both request and proxy URLs");
            std::process::exit(1);
        }
    };
    let listen_url = listen_url.to_string_lossy().into_owned();
    let exit_code = match runtime.block_on(nova_executor_server::run_main_with_telemetry(
        &listen_url,
        runtime_paths,
        ExecServerTelemetry::default(),
        http_client_factory,
        request_dispatch_mode,
    )) {
        Ok(()) => 0,
        Err(error) => {
            eprintln!("exec-server failed: {error}");
            1
        }
    };
    std::process::exit(exit_code);
}
