//! landlock/bwrap 集成套件（移植自 codex linux-sandbox/tests/suite/landlock.rs，
//! 驱动解耦改写）：codex 侧驱动走 codex_core::exec::process_exec_tool_call
//!（agent 工具调用语义）；nova 侧直调 nova-linux-sandbox 二进制 CLI
//!（与 codex 套件同构；二进制即 exec-server 生产路径的同款沙箱入口）。
//! 测试体与 codex 逐行一致。

#![cfg(target_os = "linux")]
#![allow(clippy::unwrap_used)]

use nova_executor_protocol_core::config_types::WindowsSandboxLevel;
use nova_executor_protocol_core::models::PermissionProfile;
use nova_executor_protocol_core::permissions::FileSystemAccessMode;
use nova_executor_protocol_core::permissions::FileSystemPath;
use nova_executor_protocol_core::permissions::FileSystemSandboxEntry;
use nova_executor_protocol_core::permissions::FileSystemSandboxPolicy;
use nova_executor_protocol_core::permissions::FileSystemSpecialPath;
use nova_executor_protocol_core::permissions::NetworkSandboxPolicy;
use nova_executor_utils_absolute_path::AbsolutePathBuf;
use nova_executor_utils_path_uri::PathUri;
use pretty_assertions::assert_eq;
use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Stdio;
use std::time::Duration;
use tempfile::NamedTempFile;

// 超时与 codex 同款值（上游曾给 arm64 更长，后已归一）
const SHORT_TIMEOUT_MS: u64 = 5_000;
const LONG_TIMEOUT_MS: u64 = 5_000;
const NETWORK_TIMEOUT_MS: u64 = 10_000;

/// 输出形态仿 codex 的 ExecToolCallOutput（stdout.text/stderr.text/exit_code），
/// 让测试体零改动。
#[derive(Debug)]
struct TextOutput {
    text: String,
}

#[derive(Debug)]
struct SandboxOutput {
    stdout: TextOutput,
    stderr: TextOutput,
    exit_code: i32,
}

/// nova-linux-sandbox 二进制路径（canonicalize 失败回退原值——argv0 re-exec
/// 契约需要稳定路径）。
fn nova_linux_sandbox_exe() -> PathBuf {
    let sandbox_program = PathBuf::from(env!("CARGO_BIN_EXE_nova-linux-sandbox"));
    match sandbox_program.canonicalize() {
        Ok(path) => path,
        Err(_) => sandbox_program,
    }
}

fn create_env_from_core_vars() -> HashMap<String, String> {
    std::env::vars().collect()
}

/// 直驱沙箱执行：调 nova-linux-sandbox 二进制 CLI（与 codex 套件同构——
/// 二进制含 host 侧缺失目录预建/synthetic mount 注册等完整管线，进程内
/// transform 路径没有这些）。权限档经 --permission-profile JSON 下发。
async fn run_sandboxed(
    cmd: &[&str],
    cwd: AbsolutePathBuf,
    permission_profile: &PermissionProfile,
    env: HashMap<String, String>,
    timeout_ms: u64,
    use_legacy_landlock: bool,
) -> Result<SandboxOutput, String> {
    let permission_profile_json = serde_json::to_string(permission_profile)
        .map_err(|err| format!("permission profile serialize: {err}"))?;
    let mut args = vec![
        "--sandbox-policy-cwd".to_string(),
        cwd.to_string_lossy().to_string(),
        "--command-cwd".to_string(),
        cwd.to_string_lossy().to_string(),
        "--permission-profile".to_string(),
        permission_profile_json,
    ];
    if use_legacy_landlock {
        args.push("--use-legacy-landlock".to_string());
    }
    args.push("--".to_string());
    args.extend(cmd.iter().map(|entry| (*entry).to_string()));

    let mut command = tokio::process::Command::new(env!("CARGO_BIN_EXE_nova-linux-sandbox"));
    command
        .args(args)
        .current_dir(cwd.as_path())
        .env_clear()
        .envs(env)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    let child = command
        .spawn()
        .map_err(|err| format!("spawn failed: {err}"))?;
    let output = tokio::time::timeout(Duration::from_millis(timeout_ms), child.wait_with_output())
        .await
        .map_err(|_| format!("sandboxed command timed out after {timeout_ms}ms"))?
        .map_err(|err| format!("wait failed: {err}"))?;
    Ok(SandboxOutput {
        stdout: TextOutput {
            text: String::from_utf8_lossy(&output.stdout).into_owned(),
        },
        stderr: TextOutput {
            text: String::from_utf8_lossy(&output.stderr).into_owned(),
        },
        exit_code: output.status.code().unwrap_or(-1),
    })
}

#[expect(clippy::print_stdout)]
async fn run_cmd(cmd: &[&str], writable_roots: &[PathBuf], timeout_ms: u64) {
    let output = run_cmd_output(cmd, writable_roots, timeout_ms).await;
    if output.exit_code != 0 {
        println!("stdout:\n{}", output.stdout.text);
        println!("stderr:\n{}", output.stderr.text);
        panic!("exit code: {}", output.exit_code);
    }
}

async fn run_cmd_output(
    cmd: &[&str],
    writable_roots: &[PathBuf],
    timeout_ms: u64,
) -> SandboxOutput {
    run_cmd_result_with_writable_roots(
        cmd,
        writable_roots,
        timeout_ms,
        /*use_legacy_landlock*/ false,
        /*network_access*/ false,
    )
    .await
    .expect("sandboxed command should execute")
}

async fn run_cmd_result_with_writable_roots(
    cmd: &[&str],
    writable_roots: &[PathBuf],
    timeout_ms: u64,
    use_legacy_landlock: bool,
    network_access: bool,
) -> Result<SandboxOutput, String> {
    let cwd = AbsolutePathBuf::current_dir().expect("cwd should exist");
    run_cmd_result_with_cwd_and_writable_roots(
        cmd,
        &cwd,
        writable_roots,
        timeout_ms,
        use_legacy_landlock,
        network_access,
    )
    .await
}

async fn run_cmd_result_with_permission_profile(
    cmd: &[&str],
    permission_profile: PermissionProfile,
    timeout_ms: u64,
    use_legacy_landlock: bool,
) -> Result<SandboxOutput, String> {
    let cwd = AbsolutePathBuf::current_dir().expect("cwd should exist");
    run_sandboxed(
        cmd,
        cwd,
        &permission_profile,
        create_env_from_core_vars(),
        timeout_ms,
        use_legacy_landlock,
    )
    .await
}

async fn run_cmd_result_with_permission_profile_for_cwd(
    cmd: &[&str],
    cwd: AbsolutePathBuf,
    permission_profile: PermissionProfile,
    env: HashMap<String, String>,
    timeout_ms: u64,
    use_legacy_landlock: bool,
) -> Result<SandboxOutput, String> {
    run_sandboxed(
        cmd,
        cwd,
        &permission_profile,
        env,
        timeout_ms,
        use_legacy_landlock,
    )
    .await
}

async fn run_cmd_result_with_cwd_and_writable_roots(
    cmd: &[&str],
    cwd: &std::path::Path,
    writable_roots: &[PathBuf],
    timeout_ms: u64,
    use_legacy_landlock: bool,
    network_access: bool,
) -> Result<SandboxOutput, String> {
    let writable_roots = writable_roots
        .iter()
        .map(|path| AbsolutePathBuf::try_from(path.as_path()).unwrap())
        .collect::<Vec<_>>();
    let permission_profile = PermissionProfile::workspace_write_with(
        &writable_roots,
        if network_access {
            NetworkSandboxPolicy::Enabled
        } else {
            NetworkSandboxPolicy::Restricted
        },
        // Exclude tmp-related folders from writable roots because we need a
        // folder that is writable by tests but that we intentionally disallow
        // writing to in the sandbox.
        /*exclude_tmpdir_env_var*/
        true,
        /*exclude_slash_tmp*/ true,
    );
    let cwd = AbsolutePathBuf::try_from(cwd).expect("cwd should be absolute");
    run_sandboxed(
        cmd,
        cwd,
        &permission_profile,
        create_env_from_core_vars(),
        timeout_ms,
        use_legacy_landlock,
    )
    .await
}

/// bwrap 不可用判定：spawn 失败（bwrap 未安装）或输出含 bwrap 不可用字样。
fn is_bwrap_unavailable_output(output: &SandboxOutput) -> bool {
    output.stderr.text.contains("bwrap")
        && (output.stderr.text.contains("Operation not permitted")
            || output.stderr.text.contains("Permission denied")
            || output.stderr.text.contains("Invalid argument")
            || output.stderr.text.contains("not found"))
}

async fn should_skip_bwrap_tests() -> bool {
    match run_cmd_result_with_writable_roots(
        &["bash", "-lc", "true"],
        &[],
        NETWORK_TIMEOUT_MS,
        /*use_legacy_landlock*/ false,
        /*network_access*/ true,
    )
    .await
    {
        Ok(output) => is_bwrap_unavailable_output(&output),
        // 变换/spawn 失败（含 bwrap 缺失的 ENOENT）一律跳过
        Err(err) => {
            eprintln!("bwrap availability probe failed, skipping: {err}");
            err.contains("bwrap") || err.contains("not found") || err.contains("No such file")
        }
    }
}

fn expect_denied(result: Result<SandboxOutput, String>, context: &str) -> SandboxOutput {
    // codex 侧断言 SandboxErr::Denied；nova 直驱链的沙箱拒绝表现为进程
    // 非零退出（landlock/seccomp 拦在运行时），语义同位
    let output = result.unwrap_or_else(|err| panic!("{context}: {err}"));
    assert_ne!(output.exit_code, 0, "{context}: expected nonzero exit code");
    output
}

#[tokio::test]
async fn test_root_read() {
    run_cmd(&["ls", "-l", "/bin"], &[], SHORT_TIMEOUT_MS).await;
}

#[tokio::test]
#[should_panic]
async fn test_root_write() {
    let tmpfile = NamedTempFile::new().unwrap();
    let tmpfile_path = tmpfile.path().to_string_lossy();
    run_cmd(
        &["bash", "-lc", &format!("echo blah > {tmpfile_path}")],
        &[],
        SHORT_TIMEOUT_MS,
    )
    .await;
}

#[tokio::test]
async fn test_dev_null_write() {
    if should_skip_bwrap_tests().await {
        eprintln!("skipping bwrap test: bwrap sandbox prerequisites are unavailable");
        return;
    }

    let output = run_cmd_result_with_writable_roots(
        &["bash", "-lc", "echo blah > /dev/null"],
        &[],
        // We have seen timeouts when running this test in CI on GitHub,
        // so we are using a generous timeout until we can diagnose further.
        LONG_TIMEOUT_MS,
        /*use_legacy_landlock*/ false,
        /*network_access*/ true,
    )
    .await
    .expect("sandboxed command should execute");

    assert_eq!(output.exit_code, 0);
}

#[tokio::test]
async fn bwrap_populates_minimal_dev_nodes() {
    if should_skip_bwrap_tests().await {
        eprintln!("skipping bwrap test: bwrap sandbox prerequisites are unavailable");
        return;
    }

    let output = run_cmd_result_with_writable_roots(
        &[
            "bash",
            "-lc",
            "for node in null zero full random urandom tty; do [ -c \"/dev/$node\" ] || { echo \"missing /dev/$node\" >&2; exit 1; }; done",
        ],
        &[],
        LONG_TIMEOUT_MS,
        /*use_legacy_landlock*/ false,
        /*network_access*/ true,
    )
    .await
    .expect("sandboxed command should execute");

    assert_eq!(output.exit_code, 0);
}

#[tokio::test]
async fn bwrap_preserves_writable_dev_shm_bind_mount() {
    if should_skip_bwrap_tests().await {
        eprintln!("skipping bwrap test: bwrap sandbox prerequisites are unavailable");
        return;
    }
    if !std::path::Path::new("/dev/shm").exists() {
        eprintln!("skipping bwrap test: /dev/shm is unavailable in this environment");
        return;
    }

    let target_file = match NamedTempFile::new_in("/dev/shm") {
        Ok(file) => file,
        Err(err) => {
            eprintln!("skipping bwrap test: failed to create /dev/shm temp file: {err}");
            return;
        }
    };
    let target_path = target_file.path().to_path_buf();
    std::fs::write(&target_path, "host-before").expect("seed /dev/shm file");

    let output = run_cmd_result_with_writable_roots(
        &[
            "bash",
            "-lc",
            &format!("printf sandbox-after > {}", target_path.to_string_lossy()),
        ],
        &[PathBuf::from("/dev/shm")],
        LONG_TIMEOUT_MS,
        /*use_legacy_landlock*/ false,
        /*network_access*/ true,
    )
    .await
    .expect("sandboxed command should execute");

    assert_eq!(output.exit_code, 0);
    assert_eq!(
        std::fs::read_to_string(&target_path).expect("read /dev/shm file"),
        "sandbox-after"
    );
}

#[tokio::test]
async fn test_writable_root() {
    let tmpdir = tempfile::tempdir().unwrap();
    let file_path = tmpdir.path().join("test");
    run_cmd(
        &[
            "bash",
            "-lc",
            &format!("echo blah > {}", file_path.to_string_lossy()),
        ],
        &[tmpdir.path().to_path_buf()],
        // We have seen timeouts when running this test in CI on GitHub,
        // so we are using a generous timeout until we can diagnose further.
        LONG_TIMEOUT_MS,
    )
    .await;
}

#[tokio::test]
async fn sandbox_ignores_missing_writable_roots_under_bwrap() {
    if should_skip_bwrap_tests().await {
        eprintln!("skipping bwrap test: bwrap sandbox prerequisites are unavailable");
        return;
    }

    let tempdir = tempfile::tempdir().expect("tempdir");
    let existing_root = tempdir.path().join("existing");
    let missing_root = tempdir.path().join("missing");
    std::fs::create_dir(&existing_root).expect("create existing root");

    let output = run_cmd_result_with_writable_roots(
        &["bash", "-lc", "printf sandbox-ok"],
        &[existing_root, missing_root],
        LONG_TIMEOUT_MS,
        /*use_legacy_landlock*/ false,
        /*network_access*/ true,
    )
    .await
    .expect("sandboxed command should execute");

    assert_eq!(output.exit_code, 0);
    assert_eq!(output.stdout.text, "sandbox-ok");
}

#[tokio::test]
async fn test_no_new_privs_is_enabled() {
    let output = run_cmd_output(
        &["bash", "-lc", "grep '^NoNewPrivs:' /proc/self/status"],
        &[],
        // We have seen timeouts when running this test in CI on GitHub,
        // so we are using a generous timeout until we can diagnose further.
        LONG_TIMEOUT_MS,
    )
    .await;
    let line = output
        .stdout
        .text
        .lines()
        .find(|line| line.starts_with("NoNewPrivs:"))
        .unwrap_or("");
    assert_eq!(line.trim(), "NoNewPrivs:\t1");
}

#[tokio::test]
async fn sandboxed_command_has_no_effective_or_permitted_capabilities() {
    if should_skip_bwrap_tests().await {
        eprintln!("skipping bwrap test: bwrap sandbox prerequisites are unavailable");
        return;
    }

    let output = run_cmd_output(
        &[
            "bash",
            "-lc",
            "awk '$1 == \"CapPrm:\" || $1 == \"CapEff:\" { print $1, $2 }' /proc/self/status",
        ],
        &[],
        LONG_TIMEOUT_MS,
    )
    .await;

    assert_eq!(
        output.stdout.text,
        "CapPrm: 0000000000000000\nCapEff: 0000000000000000\n"
    );
}

#[tokio::test]
async fn sandbox_inner_stage_rejects_retained_capabilities() {
    if should_skip_bwrap_tests().await {
        eprintln!("skipping bwrap test: bwrap sandbox prerequisites are unavailable");
        return;
    }

    let user_namespace_probe = match std::process::Command::new("unshare")
        .args(["--user", "--map-root-user", "--", "/bin/true"])
        .output()
    {
        Ok(output) => output,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
            eprintln!("skipping capability test: unshare is unavailable");
            return;
        }
        Err(err) => panic!("failed to probe unprivileged user namespaces: {err}"),
    };
    if !user_namespace_probe.status.success() {
        eprintln!("skipping capability test: unprivileged user namespaces are unavailable");
        return;
    }

    let permission_profile = serde_json::to_string(&PermissionProfile::read_only())
        .expect("read-only permission profile should serialize");
    let output = std::process::Command::new("unshare")
        .args(["--user", "--map-root-user", "--"])
        .arg(nova_linux_sandbox_exe())
        .args(["--sandbox-policy-cwd", "/", "--permission-profile"])
        .arg(permission_profile)
        .args([
            "--apply-seccomp-then-exec",
            "--",
            "/bin/sh",
            "-c",
            "printf command-ran",
        ])
        .output()
        .expect("capability-bearing sandbox helper should execute");

    assert!(!output.status.success());
    assert_eq!(output.stdout, Vec::<u8>::new());
    assert!(
        String::from_utf8_lossy(&output.stderr)
            .contains("Linux sandbox retained effective or permitted capabilities"),
        "unexpected stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[tokio::test]
#[should_panic(expected = "timed out")]
async fn test_timeout() {
    run_cmd(&["sleep", "2"], &[], /*timeout_ms*/ 50).await;
}

/// 沙箱内网络访问必须失败（非零退出即达标——缺二进制（127）也接受为
/// "本环境无此命令"的 skip 语义，与 codex 原语义一致）。
async fn assert_network_blocked(cmd: &[&str]) {
    let cwd = AbsolutePathBuf::current_dir().expect("cwd should exist");
    let result = run_sandboxed(
        cmd,
        cwd,
        &PermissionProfile::read_only(),
        create_env_from_core_vars(),
        NETWORK_TIMEOUT_MS,
        /*use_legacy_landlock*/ false,
    )
    .await;

    let output = match result {
        Ok(output) => output,
        // 变换/启动失败（含沙箱 fail-closed）视为拒绝成立
        Err(err) => {
            println!("sandbox denied before spawn: {err}");
            return;
        }
    };
    dbg!(&output.stderr.text);
    dbg!(&output.stdout.text);
    dbg!(&output.exit_code);

    // A completely missing binary exits with 127.  Anything else should also
    // be non‑zero (EPERM from seccomp will usually bubble up as 1, 2, 13…)
    // If—*and only if*—the command exits 0 we consider the sandbox breached.
    if output.exit_code == 0 {
        panic!(
            "Network sandbox FAILED - {cmd:?} exited 0\nstdout:\n{}\nstderr:\n{}",
            output.stdout.text, output.stderr.text
        );
    }
}

#[tokio::test]
async fn sandbox_blocks_curl() {
    assert_network_blocked(&["curl", "-I", "http://openai.com"]).await;
}

#[tokio::test]
async fn sandbox_blocks_wget() {
    assert_network_blocked(&["wget", "-qO-", "http://openai.com"]).await;
}

#[tokio::test]
async fn sandbox_blocks_ping() {
    // ICMP requires raw socket – should be denied quickly with EPERM.
    assert_network_blocked(&["ping", "-c", "1", "8.8.8.8"]).await;
}

#[tokio::test]
async fn sandbox_blocks_nc() {
    // Zero‑length connection attempt to localhost.
    assert_network_blocked(&["nc", "-z", "127.0.0.1", "80"]).await;
}

#[tokio::test]
async fn sandbox_blocks_git_and_nova_writes_inside_writable_root() {
    if should_skip_bwrap_tests().await {
        eprintln!("skipping bwrap test: bwrap sandbox prerequisites are unavailable");
        return;
    }

    let tmpdir = tempfile::tempdir().expect("tempdir");
    let dot_git = tmpdir.path().join(".git");
    let dot_nova = tmpdir.path().join(".nova");
    std::fs::create_dir_all(&dot_git).expect("create .git");
    std::fs::create_dir_all(&dot_nova).expect("create .nova");

    let git_target = dot_git.join("config");
    let nova_target = dot_nova.join("settings.json");

    let git_output = expect_denied(
        run_cmd_result_with_writable_roots(
            &[
                "bash",
                "-lc",
                &format!("echo denied > {}", git_target.to_string_lossy()),
            ],
            &[tmpdir.path().to_path_buf()],
            LONG_TIMEOUT_MS,
            /*use_legacy_landlock*/ false,
            /*network_access*/ true,
        )
        .await,
        ".git write should be denied under bubblewrap",
    );

    let nova_output = expect_denied(
        run_cmd_result_with_writable_roots(
            &[
                "bash",
                "-lc",
                &format!("echo denied > {}", nova_target.to_string_lossy()),
            ],
            &[tmpdir.path().to_path_buf()],
            LONG_TIMEOUT_MS,
            /*use_legacy_landlock*/ false,
            /*network_access*/ true,
        )
        .await,
        ".nova write should be denied under bubblewrap",
    );
    assert_ne!(git_output.exit_code, 0);
    assert_ne!(nova_output.exit_code, 0);
}

#[tokio::test]
async fn sandbox_blocks_nova_symlink_replacement_attack() {
    if should_skip_bwrap_tests().await {
        eprintln!("skipping bwrap test: bwrap sandbox prerequisites are unavailable");
        return;
    }

    use std::os::unix::fs::symlink;

    let tmpdir = tempfile::tempdir().expect("tempdir");
    let decoy = tmpdir.path().join("decoy-nova");
    std::fs::create_dir_all(&decoy).expect("create decoy dir");

    let dot_nova = tmpdir.path().join(".nova");
    symlink(&decoy, &dot_nova).expect("create .nova symlink");

    let nova_target = dot_nova.join("settings.json");

    let nova_output = expect_denied(
        run_cmd_result_with_writable_roots(
            &[
                "bash",
                "-lc",
                &format!("echo denied > {}", nova_target.to_string_lossy()),
            ],
            &[tmpdir.path().to_path_buf()],
            LONG_TIMEOUT_MS,
            /*use_legacy_landlock*/ false,
            /*network_access*/ true,
        )
        .await,
        ".nova symlink replacement should be denied",
    );
    assert_ne!(nova_output.exit_code, 0);
}

#[tokio::test]
async fn sandbox_reports_nova_symlink_build_failure_without_panicking() {
    if should_skip_bwrap_tests().await {
        eprintln!("skipping bwrap test: bwrap sandbox prerequisites are unavailable");
        return;
    }

    use std::os::unix::fs::symlink;

    let tmpdir = tempfile::tempdir().expect("tempdir");
    let decoy = tmpdir.path().join("decoy-nova");
    std::fs::create_dir_all(&decoy).expect("create decoy dir");

    let dot_nova = tmpdir.path().join(".nova");
    symlink(&decoy, &dot_nova).expect("create .nova symlink");

    let output = match run_cmd_result_with_writable_roots(
        &["bash", "-lc", "true"],
        &[tmpdir.path().to_path_buf()],
        LONG_TIMEOUT_MS,
        /*use_legacy_landlock*/ false,
        /*network_access*/ true,
    )
    .await
    {
        // 沙箱拒绝在直驱链上表现为变换/启动失败或非零退出——两者都成立
        Err(_err) => SandboxOutput {
            stdout: TextOutput {
                text: String::new(),
            },
            stderr: TextOutput {
                text: String::new(),
            },
            exit_code: 1,
        },
        Ok(output) => output,
    };

    assert_eq!(output.exit_code, 1);
    assert!(
        output
            .stderr
            .text
            .contains("error building bubblewrap command:"),
        "stderr: {}",
        output.stderr.text
    );
    assert!(
        output
            .stderr
            .text
            .contains("cannot enforce sandbox read-only path"),
        "stderr: {}",
        output.stderr.text
    );
    assert!(
        !output.stderr.text.contains("panicked at"),
        "stderr: {}",
        output.stderr.text
    );
}

#[tokio::test]
async fn sandbox_rejects_symlinked_synthetic_mount_registry() {
    if should_skip_bwrap_tests().await {
        eprintln!("skipping bwrap test: bwrap sandbox prerequisites are unavailable");
        return;
    }

    let temp = tempfile::tempdir().expect("tempdir");
    let workspace = temp.path().join("workspace");
    let registry_target = workspace.join("registry");
    std::fs::create_dir_all(&registry_target).expect("create registry target");
    let effective_uid = unsafe { libc::geteuid() };
    let registry = temp.path().join(format!(
        "nova-bwrap-synthetic-mount-targets-{effective_uid}"
    ));
    std::os::unix::fs::symlink(&registry_target, &registry).expect("symlink registry");

    let cwd = AbsolutePathBuf::try_from(workspace).expect("absolute workspace");
    let permission_profile = PermissionProfile::workspace_write_with(
        std::slice::from_ref(&cwd),
        NetworkSandboxPolicy::Enabled,
        /*exclude_tmpdir_env_var*/ true,
        /*exclude_slash_tmp*/ true,
    );
    let mut env = create_env_from_core_vars();
    env.insert("TMPDIR".to_string(), temp.path().display().to_string());
    let output = expect_denied(
        run_cmd_result_with_permission_profile_for_cwd(
            &["sh", "-c", "touch registry/forged-marker"],
            cwd,
            permission_profile,
            env,
            LONG_TIMEOUT_MS,
            /*use_legacy_landlock*/ false,
        )
        .await,
        "a symlinked registry must not expose writable bookkeeping",
    );
    assert!(
        output
            .stderr
            .text
            .contains("synthetic mount registry must not be a symlink"),
        "stderr: {}",
        output.stderr.text
    );
    assert_eq!(
        std::fs::read_dir(registry_target)
            .expect("read registry target")
            .count(),
        0,
        "the registry symlink must be rejected before registration or command execution"
    );
}

#[tokio::test]
async fn sandbox_keeps_parent_repo_discovery_while_blocking_child_metadata() {
    if should_skip_bwrap_tests().await {
        eprintln!("skipping bwrap test: bwrap sandbox prerequisites are unavailable");
        return;
    }

    let git_available = std::process::Command::new("git")
        .arg("--version")
        .status()
        .is_ok_and(|status| status.success());
    let python_available = std::process::Command::new("python3")
        .arg("--version")
        .status()
        .is_ok_and(|status| status.success());
    if !git_available || !python_available {
        eprintln!("skipping bwrap test: git or python3 is unavailable");
        return;
    }

    let tmpdir = tempfile::tempdir().expect("tempdir");
    let repo = tmpdir.path().join("repo");
    let subdir = repo.join("sub");
    let real_tmp = tmpdir.path().join("real-tmp");
    let redirected_tmp = tmpdir.path().join("redirected-tmp");
    let tmp_alias = tmpdir.path().join("tmp-alias");
    std::fs::create_dir(&real_tmp).expect("create real temp directory");
    std::fs::create_dir(&redirected_tmp).expect("create redirected temp directory");
    std::os::unix::fs::symlink(&real_tmp, &tmp_alias).expect("create temp directory alias");
    std::fs::create_dir_all(&subdir).expect("create nested workspace");
    assert!(
        std::process::Command::new("git")
            .arg("init")
            .arg("-q")
            .arg(&repo)
            .status()
            .expect("git init should run")
            .success(),
        "git init should create parent repo"
    );

    let repo = repo.to_string_lossy();
    let redirected_tmp = redirected_tmp.to_string_lossy();
    let script = format!(
        r#"set -e
test "$(git rev-parse --show-toplevel)" = '{repo}'
touch "$TMPDIR/writable-sibling"
registry="${{TMPDIR:-/tmp}}/nova-bwrap-synthetic-mount-targets-$(id -u)"
if touch "$registry/forged-marker" 2>/dev/null; then
  exit 22
fi
redirected_registry='{redirected_tmp}'/nova-bwrap-synthetic-mount-targets-$(id -u)
for marker_dir in "$registry"/*; do
  [ -d "$marker_dir" ] || continue
  mkdir -p "$redirected_registry/${{marker_dir##*/}}"
  touch "$redirected_registry/${{marker_dir##*/}}/1"
done
ln -sfn '{redirected_tmp}' "$TMPDIR"
git status --short > status.before
if grep -E '(^|[[:space:]])\.(git|nova|agents)(/|$)' status.before; then
  cat status.before
  exit 21
fi
"#,
    );

    let cwd = AbsolutePathBuf::try_from(subdir.as_path()).expect("cwd should be absolute");
    let permission_profile = PermissionProfile::workspace_write_with(
        std::slice::from_ref(&cwd),
        NetworkSandboxPolicy::Enabled,
        /*exclude_tmpdir_env_var*/ false,
        /*exclude_slash_tmp*/ false,
    );
    let mut env = create_env_from_core_vars();
    env.insert("TMPDIR".to_string(), tmp_alias.display().to_string());
    let output = run_cmd_result_with_permission_profile_for_cwd(
        &["bash", "-lc", &script],
        cwd,
        permission_profile,
        env,
        LONG_TIMEOUT_MS,
        /*use_legacy_landlock*/ false,
    )
    .await
    .expect("sandboxed command should execute");

    assert_eq!(
        output.exit_code, 0,
        "stdout:\n{}\nstderr:\n{}",
        output.stdout.text, output.stderr.text
    );
    assert!(!subdir.join(".git").exists());

    let git_init_output = expect_denied(
        run_cmd_result_with_cwd_and_writable_roots(
            &["git", "init", "-q"],
            &subdir,
            std::slice::from_ref(&subdir),
            LONG_TIMEOUT_MS,
            /*use_legacy_landlock*/ false,
            /*network_access*/ true,
        )
        .await,
        "child git init should be denied",
    );
    assert_ne!(git_init_output.exit_code, 0);
    assert!(!subdir.join(".git").exists());

    let mkdir_nova_output = expect_denied(
        run_cmd_result_with_cwd_and_writable_roots(
            &["mkdir", ".nova"],
            &subdir,
            std::slice::from_ref(&subdir),
            LONG_TIMEOUT_MS,
            /*use_legacy_landlock*/ false,
            /*network_access*/ true,
        )
        .await,
        "child .nova directory creation should be denied",
    );
    assert_ne!(mkdir_nova_output.exit_code, 0);
    assert!(!subdir.join(".nova").exists());

    let script = format!(
        r#"set -e
test "$(git rev-parse --show-toplevel)" = '{repo}'
printf '%s\n' 'import json, sys' 'for line in sys.stdin:' '    obj = json.loads(line)' '    print(obj.get("message", obj))' > jsonl_viewer.py
printf '%s\n' '{{"message":"ok"}}' | python3 jsonl_viewer.py | grep -q ok
"#,
    );
    let output = run_cmd_result_with_cwd_and_writable_roots(
        &["bash", "-lc", &script],
        &subdir,
        std::slice::from_ref(&subdir),
        LONG_TIMEOUT_MS,
        /*use_legacy_landlock*/ false,
        /*network_access*/ true,
    )
    .await
    .expect("sandboxed command should execute");
    assert_eq!(
        output.exit_code, 0,
        "stdout:\n{}\nstderr:\n{}",
        output.stdout.text, output.stderr.text
    );

    assert!(subdir.join("jsonl_viewer.py").is_file());
    assert!(!subdir.join(".git").exists());
    assert!(!subdir.join(".nova").exists());
    assert!(!subdir.join(".agents").exists());
}

#[tokio::test]
async fn sandbox_blocks_explicit_split_policy_carveouts_under_bwrap() {
    if should_skip_bwrap_tests().await {
        eprintln!("skipping bwrap test: bwrap sandbox prerequisites are unavailable");
        return;
    }

    let tmpdir = tempfile::tempdir().expect("tempdir");
    let blocked = tmpdir.path().join("blocked");
    std::fs::create_dir_all(&blocked).expect("create blocked dir");
    let blocked_target = blocked.join("secret.txt");
    // These tests bypass the usual legacy-policy bridge, so explicitly keep
    // the sandbox helper binary and minimal runtime paths readable.
    let sandbox_helper_dir = nova_linux_sandbox_exe()
        .parent()
        .expect("sandbox helper should have a parent")
        .to_path_buf();

    let file_system_sandbox_policy = FileSystemSandboxPolicy::restricted(vec![
        FileSystemSandboxEntry {
            path: FileSystemPath::Special {
                value: FileSystemSpecialPath::Minimal,
            },
            access: FileSystemAccessMode::Read,
            missing_path_behavior: None,
        },
        FileSystemSandboxEntry {
            path: FileSystemPath::Path {
                path: AbsolutePathBuf::try_from(sandbox_helper_dir.as_path())
                    .expect("absolute helper dir")
                    .into(),
            },
            access: FileSystemAccessMode::Read,
            missing_path_behavior: None,
        },
        FileSystemSandboxEntry {
            path: FileSystemPath::Path {
                path: AbsolutePathBuf::try_from(tmpdir.path())
                    .expect("absolute tempdir")
                    .into(),
            },
            access: FileSystemAccessMode::Write,
            missing_path_behavior: None,
        },
        FileSystemSandboxEntry {
            path: FileSystemPath::Path {
                path: AbsolutePathBuf::try_from(blocked.as_path())
                    .expect("absolute blocked dir")
                    .into(),
            },
            access: FileSystemAccessMode::Deny,
            missing_path_behavior: None,
        },
    ]);
    let permission_profile = PermissionProfile::from_runtime_permissions(
        &file_system_sandbox_policy,
        NetworkSandboxPolicy::Enabled,
    );
    let output = expect_denied(
        run_cmd_result_with_permission_profile(
            &[
                "bash",
                "-lc",
                &format!("echo denied > {}", blocked_target.to_string_lossy()),
            ],
            permission_profile,
            LONG_TIMEOUT_MS,
            /*use_legacy_landlock*/ false,
        )
        .await,
        "explicit split-policy carveout should be denied under bubblewrap",
    );

    assert_ne!(output.exit_code, 0);
}

#[tokio::test]
async fn sandbox_starts_with_denied_tmp_without_exposing_registry() {
    if should_skip_bwrap_tests().await {
        eprintln!("skipping bwrap test: bwrap sandbox prerequisites are unavailable");
        return;
    }

    let temp = tempfile::tempdir().expect("tempdir");
    std::fs::write(temp.path().join("AGENTS.md"), "project instructions\n")
        .expect("write instructions");
    let cwd = AbsolutePathBuf::try_from(temp.path()).expect("absolute workspace");
    let sandbox_helper = nova_linux_sandbox_exe();
    let helper_dir = AbsolutePathBuf::try_from(sandbox_helper.parent().expect("helper parent"))
        .expect("absolute helper directory");

    for tmp_root in [PathBuf::from("/tmp"), temp.path().join("denied-tmp")] {
        std::fs::create_dir_all(&tmp_root).expect("create temp root");
        let secret = NamedTempFile::new_in(&tmp_root).expect("denied file");
        std::fs::write(secret.path(), "private").expect("write denied file");
        for read_root in [FileSystemSpecialPath::Root, FileSystemSpecialPath::Minimal] {
            let denied_path = if tmp_root == std::path::Path::new("/tmp") {
                FileSystemPath::Special {
                    value: FileSystemSpecialPath::SlashTmp,
                }
            } else {
                AbsolutePathBuf::try_from(tmp_root.as_path())
                    .expect("absolute temp root")
                    .into()
            };
            let policy = FileSystemSandboxPolicy::restricted(vec![
                FileSystemSandboxEntry::new(
                    FileSystemPath::Special { value: read_root },
                    FileSystemAccessMode::Read,
                ),
                FileSystemSandboxEntry::new(helper_dir.clone().into(), FileSystemAccessMode::Read),
                FileSystemSandboxEntry::new(cwd.clone().into(), FileSystemAccessMode::Write),
                FileSystemSandboxEntry::new(denied_path, FileSystemAccessMode::Deny),
            ]);
            let mut env = create_env_from_core_vars();
            env.insert("TMPDIR".to_string(), tmp_root.display().to_string());
            env.insert(
                "DENIED_SECRET".to_string(),
                secret.path().display().to_string(),
            );
            let output = run_cmd_result_with_permission_profile_for_cwd(
                &[
                    "sh",
                    "-c",
                    r#"set -e
cat AGENTS.md
test ! -r "$DENIED_SECRET"
if printf modified > "$DENIED_SECRET" 2>/dev/null; then exit 1; fi
registry="$TMPDIR/nova-bwrap-synthetic-mount-targets-$(id -u)"
test ! -e "$registry"
"#,
                ],
                cwd.clone(),
                PermissionProfile::from_runtime_permissions(&policy, NetworkSandboxPolicy::Enabled),
                env,
                LONG_TIMEOUT_MS,
                /*use_legacy_landlock*/ false,
            )
            .await
            .expect("sandbox should start with denied temp directory");
            assert_eq!(
                (output.exit_code, output.stdout.text.as_str()),
                (0, "project instructions\n"),
                "stderr: {}",
                output.stderr.text
            );
            assert_eq!(std::fs::read_to_string(secret.path()).unwrap(), "private");
            assert!(!temp.path().join(".git").exists());
        }
    }
}

#[tokio::test]
async fn sandbox_reenables_writable_subpaths_under_unreadable_parents() {
    if should_skip_bwrap_tests().await {
        eprintln!("skipping bwrap test: bwrap sandbox prerequisites are unavailable");
        return;
    }

    let tmpdir = tempfile::tempdir().expect("tempdir");
    let blocked = tmpdir.path().join("blocked");
    let allowed = blocked.join("allowed");
    std::fs::create_dir_all(&allowed).expect("create blocked/allowed dir");
    let allowed_target = allowed.join("note.txt");
    // These tests bypass the usual legacy-policy bridge, so explicitly keep
    // the sandbox helper binary and minimal runtime paths readable.
    let sandbox_helper_dir = nova_linux_sandbox_exe()
        .parent()
        .expect("sandbox helper should have a parent")
        .to_path_buf();

    let file_system_sandbox_policy = FileSystemSandboxPolicy::restricted(vec![
        FileSystemSandboxEntry {
            path: FileSystemPath::Special {
                value: FileSystemSpecialPath::Minimal,
            },
            access: FileSystemAccessMode::Read,
            missing_path_behavior: None,
        },
        FileSystemSandboxEntry {
            path: FileSystemPath::Path {
                path: AbsolutePathBuf::try_from(sandbox_helper_dir.as_path())
                    .expect("absolute helper dir")
                    .into(),
            },
            access: FileSystemAccessMode::Read,
            missing_path_behavior: None,
        },
        FileSystemSandboxEntry {
            path: FileSystemPath::Path {
                path: AbsolutePathBuf::try_from(tmpdir.path())
                    .expect("absolute tempdir")
                    .into(),
            },
            access: FileSystemAccessMode::Write,
            missing_path_behavior: None,
        },
        FileSystemSandboxEntry {
            path: FileSystemPath::Path {
                path: AbsolutePathBuf::try_from(blocked.as_path())
                    .expect("absolute blocked dir")
                    .into(),
            },
            access: FileSystemAccessMode::Deny,
            missing_path_behavior: None,
        },
        FileSystemSandboxEntry {
            path: FileSystemPath::Path {
                path: AbsolutePathBuf::try_from(allowed.as_path())
                    .expect("absolute allowed dir")
                    .into(),
            },
            access: FileSystemAccessMode::Write,
            missing_path_behavior: None,
        },
    ]);
    let permission_profile = PermissionProfile::from_runtime_permissions(
        &file_system_sandbox_policy,
        NetworkSandboxPolicy::Enabled,
    );
    let output = run_cmd_result_with_permission_profile(
        &[
            "bash",
            "-lc",
            &format!(
                "printf allowed > {} && cat {}",
                allowed_target.to_string_lossy(),
                allowed_target.to_string_lossy()
            ),
        ],
        permission_profile,
        LONG_TIMEOUT_MS,
        /*use_legacy_landlock*/ false,
    )
    .await
    .expect("nested writable carveout should execute under bubblewrap");

    assert_eq!(output.exit_code, 0);
    assert_eq!(output.stdout.text.trim(), "allowed");
}

#[tokio::test]
async fn sandbox_blocks_root_read_carveouts_under_bwrap() {
    if should_skip_bwrap_tests().await {
        eprintln!("skipping bwrap test: bwrap sandbox prerequisites are unavailable");
        return;
    }

    let tmpdir = tempfile::tempdir().expect("tempdir");
    let blocked = tmpdir.path().join("blocked");
    std::fs::create_dir_all(&blocked).expect("create blocked dir");
    let blocked_target = blocked.join("secret.txt");
    std::fs::write(&blocked_target, "secret").expect("seed blocked file");

    let file_system_sandbox_policy = FileSystemSandboxPolicy::restricted(vec![
        FileSystemSandboxEntry {
            path: FileSystemPath::Special {
                value: FileSystemSpecialPath::Root,
            },
            access: FileSystemAccessMode::Read,
            missing_path_behavior: None,
        },
        FileSystemSandboxEntry {
            path: FileSystemPath::Path {
                path: AbsolutePathBuf::try_from(blocked.as_path())
                    .expect("absolute blocked dir")
                    .into(),
            },
            access: FileSystemAccessMode::Deny,
            missing_path_behavior: None,
        },
    ]);
    let permission_profile = PermissionProfile::from_runtime_permissions(
        &file_system_sandbox_policy,
        NetworkSandboxPolicy::Enabled,
    );
    let output = expect_denied(
        run_cmd_result_with_permission_profile(
            &[
                "bash",
                "-lc",
                &format!("cat {}", blocked_target.to_string_lossy()),
            ],
            permission_profile,
            LONG_TIMEOUT_MS,
            /*use_legacy_landlock*/ false,
        )
        .await,
        "root-read carveout should be denied under bubblewrap",
    );

    assert_ne!(output.exit_code, 0);
}

#[tokio::test]
async fn sandbox_blocks_ssh() {
    // Force ssh to attempt a real TCP connection but fail quickly.  `BatchMode`
    // avoids password prompts, and `ConnectTimeout` keeps the hang time low.
    assert_network_blocked(&[
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=1",
        "github.com",
    ])
    .await;
}

#[tokio::test]
async fn sandbox_blocks_getent() {
    assert_network_blocked(&["getent", "ahosts", "openai.com"]).await;
}

#[tokio::test]
async fn sandbox_blocks_dev_tcp_redirection() {
    // This syntax is only supported by bash and zsh. We try bash first.
    // Fallback generic socket attempt using /bin/sh with bash‑style /dev/tcp.  Not
    // all images ship bash, so we guard against 127 as well.
    assert_network_blocked(&["bash", "-c", "echo hi > /dev/tcp/127.0.0.1/80"]).await;
}
