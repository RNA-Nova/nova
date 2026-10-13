//! Mounts for writable roots must preserve read-only metadata protections.
//!
//! （对位 codex 39f4277087 `linux-sandbox/tests/suite/nested_metadata_tests.rs`。
//! 命名适配：`.codex`→`.nova`（含 nova 侧元数据配置文件名 `config.toml`→`settings.json`，
//! 与本套件 `sandbox_blocks_metadata_writes_inside_writable_root` 的既有适配一致）。）

use super::LONG_TIMEOUT_MS;
use super::create_env_from_core_vars;
use super::run_cmd_result_with_cwd_and_writable_roots;
use super::run_cmd_result_with_permission_profile_for_cwd;
use super::should_skip_bwrap_tests;
use nova_exec_server_protocol_core::models::PermissionProfile;
use nova_exec_server_protocol_core::permissions::FileSystemAccessMode;
use nova_exec_server_protocol_core::permissions::FileSystemPath;
use nova_exec_server_protocol_core::permissions::FileSystemSandboxEntry;
use nova_exec_server_protocol_core::permissions::FileSystemSandboxPolicy;
use nova_exec_server_protocol_core::permissions::FileSystemSpecialPath;
use nova_exec_server_protocol_core::permissions::NetworkSandboxPolicy;
use nova_exec_server_utils_absolute_path::AbsolutePathBuf;
use pretty_assertions::assert_eq;

enum RootLayout {
    Directory,
    Symlink,
}

#[test_case::test_case(RootLayout::Directory, ""; "home_cwd")]
#[test_case::test_case(RootLayout::Directory, ".nova/visualizations/thread"; "visualization_cwd")]
#[test_case::test_case(RootLayout::Symlink, ""; "symlink_home_cwd")]
#[test_case::test_case(RootLayout::Symlink, ".nova/visualizations/thread"; "symlink_visualization_cwd")]
#[tokio::test]
async fn sandbox_starts_with_nested_writable_metadata(layout: RootLayout, relative_cwd: &str) {
    if should_skip_bwrap_tests().await {
        eprintln!("skipping bwrap test: bwrap sandbox prerequisites are unavailable");
        return;
    }

    let temp = tempfile::tempdir().expect("tempdir");
    let home =
        AbsolutePathBuf::from_absolute_path(temp.path().join("home")).expect("absolute home");
    match layout {
        RootLayout::Directory => std::fs::create_dir(&home).expect("create home"),
        RootLayout::Symlink => {
            let target = temp.path().join("real-home");
            std::fs::create_dir(&target).expect("create real home");
            std::os::unix::fs::symlink(target, &home).expect("create home alias");
        }
    }
    let nova_home = home.join(".nova");
    let visualization = nova_home.join("visualizations/thread");
    std::fs::create_dir_all(&visualization).expect("create visualization directory");
    let protected_file = nova_home.join("settings.json");
    std::fs::write(&protected_file, "unchanged").expect("write protected config");

    let mut entries = vec![
        FileSystemSandboxEntry::new(
            FileSystemPath::Special {
                value: FileSystemSpecialPath::Root,
            },
            FileSystemAccessMode::Read,
        ),
        FileSystemSandboxEntry::new(home.clone().into(), FileSystemAccessMode::Write),
        FileSystemSandboxEntry::new(nova_home.clone().into(), FileSystemAccessMode::Read),
        FileSystemSandboxEntry::new(visualization.clone().into(), FileSystemAccessMode::Write),
    ];
    // Remote clients expand generated workspace metadata rules to concrete
    // paths, including paths that do not yet exist on the executor.
    for name in [".git", ".agents", ".nova", ".aws"] {
        entries.push(FileSystemSandboxEntry::skip_missing_path(
            visualization.join(name).into(),
            FileSystemAccessMode::Read,
        ));
    }
    let permission_profile = PermissionProfile::from_runtime_permissions(
        &FileSystemSandboxPolicy::restricted(entries),
        NetworkSandboxPolicy::Enabled,
    );
    let script = r#"
set -eu
workspace="$1"
visualization="$2"
printf home > "$workspace/allowed.txt"
printf visualization > "$visualization/allowed.txt"
if (rm "$workspace") 2>/dev/null; then
    exit 13
fi
if (printf changed > "$workspace/.nova/settings.json") 2>/dev/null; then
    exit 10
fi
if (touch "$workspace/.nova/forbidden.txt") 2>/dev/null; then
    exit 11
fi
for name in .git .agents .nova .aws; do
    test -d "$visualization/$name"
    if (touch "$visualization/$name/forbidden.txt") 2>/dev/null; then
        exit 12
    fi
done
printf nested-metadata-protected
"#;
    let output = run_cmd_result_with_permission_profile_for_cwd(
        &[
            "/bin/sh",
            "-c",
            script,
            "nested-metadata-test",
            home.to_str().expect("UTF-8 home"),
            visualization
                .to_str()
                .expect("UTF-8 visualization directory"),
        ],
        home.join(relative_cwd),
        permission_profile,
        create_env_from_core_vars(),
        LONG_TIMEOUT_MS,
        /*use_legacy_landlock*/ false,
    )
    .await
    .expect("nested writable root should start under bubblewrap");

    assert_eq!(
        (output.exit_code, output.stdout.text, output.stderr.text),
        (0, "nested-metadata-protected".to_string(), String::new())
    );
    for (path, expected) in [
        (home.join("allowed.txt"), "home"),
        (visualization.join("allowed.txt"), "visualization"),
        (protected_file, "unchanged"),
    ] {
        assert_eq!(std::fs::read_to_string(path).expect("read file"), expected);
    }
    for name in [".git", ".agents", ".nova", ".aws"] {
        assert!(
            !visualization.join(name).exists(),
            "temporary {name} mountpoint should be cleaned up"
        );
    }
}

#[test_case::test_case(RootLayout::Directory; "direct")]
#[test_case::test_case(RootLayout::Symlink; "alias_outside_writable_roots")]
#[tokio::test]
async fn workspace_write_protects_resolved_gitdir_in_another_writable_root(layout: RootLayout) {
    if should_skip_bwrap_tests().await {
        eprintln!("skipping bwrap test: bwrap sandbox prerequisites are unavailable");
        return;
    }

    let temp = tempfile::tempdir().expect("tempdir");
    // Roots at the same depth keep the workspace mount before the additional writable root.
    let workspace = temp.path().join("workspace");
    let writable = temp.path().join("writable");
    let gitdir = writable.join("gitdir");
    std::fs::create_dir(&workspace).expect("create workspace");
    std::fs::create_dir_all(gitdir.join("hooks")).expect("create Git hooks directory");
    let pointer_target = match layout {
        RootLayout::Directory => gitdir.clone(),
        RootLayout::Symlink => {
            let aliases = temp.path().join("readonly-aliases");
            std::fs::create_dir(&aliases).expect("create aliases directory");
            let alias = aliases.join("repo");
            std::os::unix::fs::symlink(&writable, &alias).expect("create gitdir alias");
            alias.join("gitdir")
        }
    };
    std::fs::write(
        workspace.join(".git"),
        format!("gitdir: {}\n", pointer_target.display()),
    )
    .expect("write Git pointer");
    let hook = gitdir.join("hooks/pre-commit");
    std::fs::write(&hook, "unchanged").expect("write Git hook");

    let script = r#"
set -eu
printf allowed > "$1/allowed.txt"
if (printf changed > "$1/gitdir/hooks/pre-commit") 2>/dev/null; then
    exit 10
fi
"#;
    let output = run_cmd_result_with_cwd_and_writable_roots(
        &[
            "/bin/sh",
            "-c",
            script,
            "resolved-gitdir-test",
            writable.to_str().expect("UTF-8 writable root"),
        ],
        &workspace,
        std::slice::from_ref(&writable),
        LONG_TIMEOUT_MS,
        /*use_legacy_landlock*/ false,
        /*network_access*/ true,
    )
    .await
    .expect("sandboxed command should complete");

    assert_eq!(
        (output.exit_code, output.stdout.text, output.stderr.text),
        (0, String::new(), String::new())
    );
    for (path, expected) in [
        (writable.join("allowed.txt"), "allowed"),
        (hook, "unchanged"),
    ] {
        assert_eq!(std::fs::read_to_string(path).expect("read file"), expected);
    }
}
