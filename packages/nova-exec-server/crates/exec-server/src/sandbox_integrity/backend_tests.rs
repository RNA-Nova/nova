//! Exercise backend policy preparation and dependency discovery.
//! （对位 codex 3342ee8c07 `sandbox_integrity/backend_tests.rs`）

use super::backend;
use nova_exec_server_protocol_core::config_types::WindowsSandboxLevel;
use nova_exec_server_protocol_core::models::PermissionProfile;
use nova_exec_server_protocol_core::permissions::FileSystemAccessMode;
use nova_exec_server_protocol_core::permissions::FileSystemPath;
use nova_exec_server_protocol_core::permissions::FileSystemSandboxEntry;
use nova_exec_server_protocol_core::permissions::FileSystemSandboxPolicy;
#[cfg(target_os = "linux")]
use nova_exec_server_protocol_core::permissions::FileSystemSpecialPath;
use nova_exec_server_protocol_core::permissions::NetworkSandboxPolicy;
use nova_exec_server_protocol_core::sandbox::SandboxOverride;
use nova_exec_server_sandboxing::FileContentsChecker;
use nova_exec_server_sandboxing::SandboxExecRequest;
use nova_exec_server_utils_absolute_path::AbsolutePathBuf;
use nova_exec_server_utils_path_uri::PathUri;
use pretty_assertions::assert_eq;
use std::fs;
use std::io;

#[test]
fn snapshot_denials_replace_patterns_including_an_empty_expansion() -> io::Result<()> {
    let temp = tempfile::tempdir()?;
    let root = AbsolutePathBuf::from_absolute_path(temp.path())?.canonicalize()?;
    let target = root.join("helper");
    fs::write(&target, "fixture")?;
    let policy = FileSystemSandboxPolicy::restricted(vec![
        FileSystemSandboxEntry::new(root.clone().into(), FileSystemAccessMode::Write),
        deny_glob(format!("{}/*", root.display())),
    ]);
    for (expanded, writable) in [(vec![], true), (vec![target.clone()], false)] {
        let prepared = policy.clone().with_expanded_deny_globs(expanded);
        let checker = FileContentsChecker::new(&prepared, &root)?;
        assert_eq!(checker.check(&target)?.is_some(), writable);
    }
    Ok(())
}

pub(super) fn deny_glob(pattern: impl Into<String>) -> FileSystemSandboxEntry {
    FileSystemSandboxEntry::new(
        FileSystemPath::GlobPattern {
            pattern: pattern.into(),
        },
        FileSystemAccessMode::Deny,
    )
}

pub(super) fn sandbox_request(
    cwd: &AbsolutePathBuf,
    policy: &FileSystemSandboxPolicy,
) -> SandboxExecRequest {
    SandboxExecRequest {
        sandbox_override: SandboxOverride::NoOverride,
        command: vec![cwd.join("not-executed").to_string_lossy().into_owned()],
        cwd: PathUri::from_abs_path(cwd),
        sandbox_policy_cwd: PathUri::from_abs_path(cwd),
        env: Default::default(),
        network: None,
        network_environment_id: None,
        sandbox: backend::SANDBOX_TYPE,
        windows_sandbox_level: WindowsSandboxLevel::Disabled,
        permission_profile: PermissionProfile::from_runtime_permissions(
            policy,
            NetworkSandboxPolicy::Restricted,
        ),
        arg0: None,
    }
}

// 对位 codex d9960e12bb：bubblewrap 后端测试。与上游的差异仅为改名纪律——
// 资源目录 codex-resources → nova-resources、假可执行文件 codex → nova。
#[cfg(target_os = "linux")]
#[test]
fn dependency_inventory_uses_launcher_location_and_command_path() -> io::Result<()> {
    use std::os::unix::fs::PermissionsExt;

    let temp = tempfile::tempdir()?;
    let root = AbsolutePathBuf::from_absolute_path(temp.path())?.canonicalize()?;
    let cwd = root.join("command");
    let tools = root.join("tools");
    let bundled = tools.join("nova-resources/bwrap");
    fs::create_dir(&cwd)?;
    fs::create_dir(&tools)?;
    fs::create_dir(tools.join("nova-resources"))?;
    for path in [
        tools.join("bwrap"),
        tools.join("rg"),
        cwd.join("rg"),
        tools.join("nova"),
        bundled.clone(),
    ] {
        fs::write(&path, "fixture")?;
        fs::set_permissions(&path, fs::Permissions::from_mode(/*mode*/ 0o755))?;
    }
    let launcher = cwd.join("sandbox-helper");
    std::os::unix::fs::symlink(tools.join("nova"), &launcher)?;
    let policy = FileSystemSandboxPolicy::restricted(vec![deny_glob("*.key")]);
    let mut request = sandbox_request(&cwd, &policy);
    request.command[0] = launcher.to_string_lossy().into_owned();
    for (path, scanner) in [
        ("../tools", tools.join("rg")),
        (":../tools", tools.join("rg")),
    ] {
        request.env.insert("PATH".to_owned(), path.to_owned());
        let dependencies = backend::critical_dependencies(&request, &policy, &cwd)?
            .into_iter()
            .filter(|(name, _)| {
                matches!(
                    *name,
                    "bundled_bwrap_candidate" | "system_bwrap_candidate" | "glob_scanner"
                )
            })
            .map(|(name, path)| path.and_then(fs::canonicalize).map(|path| (name, path)))
            .collect::<io::Result<Vec<_>>>()?;
        assert_eq!(
            dependencies,
            vec![
                ("bundled_bwrap_candidate", bundled.to_path_buf()),
                (
                    "system_bwrap_candidate",
                    tools.join("bwrap").into_path_buf()
                ),
                ("glob_scanner", scanner.into_path_buf()),
            ]
        );
    }
    Ok(())
}

// 对位 codex d9960e12bb：:tmpdir 按命令 env 处理（空值删除条目、相对值按命令
// cwd 绝对化、~ 前缀经 HOME 展开），deny glob 的 ~ 前缀同样按命令 HOME 快照。
#[cfg(target_os = "linux")]
#[test]
fn policy_preparation_uses_command_tmpdir_and_home() -> io::Result<()> {
    let temp = tempfile::tempdir()?;
    let root = AbsolutePathBuf::from_absolute_path(temp.path())?.canonicalize()?;
    let cwd = root.join("command");
    let home = root.join("home");
    fs::create_dir(&cwd)?;
    fs::create_dir(&home)?;
    let denied = home.join("secret.key");
    fs::write(&denied, "fixture")?;
    let policy = FileSystemSandboxPolicy::restricted(vec![
        FileSystemSandboxEntry::new(
            FileSystemPath::Special {
                value: FileSystemSpecialPath::Tmpdir,
            },
            FileSystemAccessMode::Write,
        ),
        deny_glob("~/secret.*"),
    ]);
    let mut request = sandbox_request(&cwd, &policy);
    request
        .env
        .insert("HOME".to_owned(), home.to_string_lossy().into_owned());
    for (tmpdir, expected) in [
        ("", None),
        ("scratch", Some(cwd.join("scratch"))),
        ("~/scratch", Some(home.join("scratch"))),
    ] {
        request.env.insert("TMPDIR".to_owned(), tmpdir.to_owned());
        let mut entries = expected
            .into_iter()
            .map(|path| FileSystemSandboxEntry::new(path.into(), FileSystemAccessMode::Write))
            .collect::<Vec<_>>();
        entries.push(FileSystemSandboxEntry::new(
            denied.clone().into(),
            FileSystemAccessMode::Deny,
        ));
        assert_eq!(
            backend::prepare_policy(&request, policy.clone(), &cwd)?,
            FileSystemSandboxPolicy::restricted(entries),
        );
    }
    Ok(())
}
