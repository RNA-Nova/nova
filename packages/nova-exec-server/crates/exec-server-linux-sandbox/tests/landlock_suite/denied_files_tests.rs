//! Multiple denied files must not prevent sandbox startup or expose protected contents.
//!
//! （对位 codex 39f4277087 `linux-sandbox/tests/suite/denied_files_tests.rs`——
//! 上游 ae7aad586e #50059 的回归套件，含 ccde2fc8b7/ac9b5b8380 的 rg 保护加固。
//! 命名适配：`codex_*`→`nova_exec_server_*`、`CODEX_TEST_*`→`NOVA_EXEC_SERVER_TEST_*`。）

use super::LONG_TIMEOUT_MS;
use super::create_env_from_core_vars;
use super::nova_linux_sandbox_exe;
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
use nova_exec_server_utils_cargo_bin::write_executable;
use pretty_assertions::assert_eq;

enum DeniedFileRules {
    ExactPaths,
    Globs,
    GlobsWithProtectedRg,
    GlobsWithNativeFallback,
}

#[test_case::test_case(DeniedFileRules::ExactPaths; "exact_paths")]
#[test_case::test_case(DeniedFileRules::Globs; "globs")]
#[test_case::test_case(DeniedFileRules::GlobsWithProtectedRg; "globs_with_protected_rg")]
#[test_case::test_case(DeniedFileRules::GlobsWithNativeFallback; "globs_with_native_fallback")]
#[tokio::test]
async fn sandbox_starts_with_multiple_denied_files(rules: DeniedFileRules) {
    if should_skip_bwrap_tests().await {
        eprintln!("skipping bwrap test: bwrap sandbox prerequisites are unavailable");
        return;
    }

    let use_controlled_rg_path = matches!(
        rules,
        DeniedFileRules::GlobsWithProtectedRg | DeniedFileRules::GlobsWithNativeFallback
    );
    let temp = if use_controlled_rg_path {
        // Bazel can mount the default temp directory noexec.
        tempfile::tempdir_in(std::env::current_dir().expect("current directory"))
            .or_else(|_| tempfile::tempdir())
    } else {
        tempfile::tempdir()
    }
    .expect("tempdir");
    let workspace = AbsolutePathBuf::try_from(temp.path()).expect("absolute workspace");
    std::fs::create_dir(workspace.join("nested")).expect("create nested directory");
    std::fs::write(workspace.join("AGENTS.md"), "project instructions\n")
        .expect("write allowed instructions");
    let denied_files = [
        (workspace.join("one.key"), "dummy first key"),
        (workspace.join("nested/two.key"), "dummy second key"),
        (workspace.join(".env.local"), "dummy environment"),
    ];
    for (path, contents) in &denied_files {
        std::fs::write(path, contents).expect("write denied dummy file");
    }

    let sandbox_helper = nova_linux_sandbox_exe();
    let helper_dir = AbsolutePathBuf::try_from(sandbox_helper.parent().expect("helper parent"))
        .expect("absolute helper directory");
    let mut entries = vec![
        FileSystemSandboxEntry::new(
            FileSystemPath::Special {
                value: FileSystemSpecialPath::Minimal,
            },
            FileSystemAccessMode::Read,
        ),
        FileSystemSandboxEntry::new(helper_dir.into(), FileSystemAccessMode::Read),
        FileSystemSandboxEntry::new(workspace.clone().into(), FileSystemAccessMode::Write),
    ];
    let mut env = create_env_from_core_vars();
    // User config must not suppress the files used to construct deny masks.
    let rg_config = workspace.join("ripgrep.conf");
    std::fs::write(rg_config.as_path(), "--quiet\n").expect("write ripgrep config");
    env.insert(
        "RIPGREP_CONFIG_PATH".to_string(),
        rg_config.to_str().expect("UTF-8 config path").to_string(),
    );
    let rg_fixture = if use_controlled_rg_path {
        let fixture = tempfile::tempdir_in(temp.path().parent().expect("workspace parent"))
            .expect("rg fixture");
        write_executable(
            workspace.join("rg").as_path(),
            "#!/bin/sh\n: > \"$NOVA_EXEC_SERVER_TEST_REJECTED_RG_MARKER\"\nexit 0\n",
        )
        .expect("write workspace rg fixture");
        if matches!(rules, DeniedFileRules::GlobsWithProtectedRg) {
            write_executable(
                &fixture.path().join("rg"),
                r#"#!/bin/sh
: > "$NOVA_EXEC_SERVER_TEST_PROTECTED_RG_MARKER"
printf '%s\000' "$NOVA_EXEC_SERVER_TEST_DENIED_PATH_0" "$NOVA_EXEC_SERVER_TEST_DENIED_PATH_1" "$NOVA_EXEC_SERVER_TEST_DENIED_PATH_2"
"#,
            )
            .expect("write protected rg fixture");
        }
        // Keep bubblewrap available without adding an unrelated rg to the test PATH.
        if let Some(bwrap) = nova_exec_server_sandboxing::find_system_bwrap_in_path(
            &PermissionProfile::read_only().file_system_sandbox_policy(),
            &std::env::current_dir().expect("current directory"),
        ) {
            std::os::unix::fs::symlink(bwrap, fixture.path().join("bwrap"))
                .expect("link system bubblewrap");
        }
        env.insert(
            "PATH".to_string(),
            std::env::join_paths([workspace.as_path(), fixture.path()])
                .expect("join PATH")
                .into_string()
                .expect("UTF-8 PATH"),
        );
        for (name, path) in [
            (
                "NOVA_EXEC_SERVER_TEST_REJECTED_RG_MARKER",
                fixture.path().join("rejected-rg-ran"),
            ),
            (
                "NOVA_EXEC_SERVER_TEST_PROTECTED_RG_MARKER",
                fixture.path().join("protected-rg-ran"),
            ),
            (
                "NOVA_EXEC_SERVER_TEST_DENIED_PATH_0",
                denied_files[0].0.to_path_buf(),
            ),
            (
                "NOVA_EXEC_SERVER_TEST_DENIED_PATH_1",
                denied_files[1].0.to_path_buf(),
            ),
            (
                "NOVA_EXEC_SERVER_TEST_DENIED_PATH_2",
                denied_files[2].0.to_path_buf(),
            ),
        ] {
            env.insert(
                name.to_string(),
                path.to_str().expect("UTF-8 fixture path").to_string(),
            );
        }
        Some(fixture)
    } else {
        None
    };
    match rules {
        DeniedFileRules::ExactPaths => {
            entries.extend(denied_files.iter().map(|(path, _)| {
                FileSystemSandboxEntry::new(path.clone().into(), FileSystemAccessMode::Deny)
            }));
        }
        DeniedFileRules::Globs
        | DeniedFileRules::GlobsWithProtectedRg
        | DeniedFileRules::GlobsWithNativeFallback => {
            for suffix in ["**/*.key", "**/.env.local"] {
                entries.push(FileSystemSandboxEntry::new(
                    FileSystemPath::GlobPattern {
                        pattern: format!("{}/{suffix}", workspace.display()),
                    },
                    FileSystemAccessMode::Deny,
                ));
            }
        }
    }
    let permission_profile = PermissionProfile::from_runtime_permissions(
        &FileSystemSandboxPolicy::restricted(entries),
        NetworkSandboxPolicy::Enabled,
    );
    if matches!(rules, DeniedFileRules::Globs)
        && nova_exec_server_sandboxing::find_pre_sandbox_executable_in_path(
            "rg",
            &permission_profile.file_system_sandbox_policy(),
            workspace.as_path(),
        )
        .is_none()
    {
        eprintln!("skipping ripgrep config test: no protected rg is available");
        return;
    }
    let output = run_cmd_result_with_permission_profile_for_cwd(
        &[
            "/bin/sh",
            "-c",
            r#"set -eu
/bin/cat AGENTS.md
for blocked in one.key nested/two.key .env.local; do
    if (: < "$blocked") 2>/dev/null; then
        printf 'read unexpectedly allowed: %s\n' "$blocked" >&2
        exit 10
    fi
    if (printf changed > "$blocked") 2>/dev/null; then
        printf 'write unexpectedly allowed: %s\n' "$blocked" >&2
        exit 11
    fi
done
printf 'allowed\n' > allowed.txt
/bin/cat allowed.txt
"#,
        ],
        workspace.clone(),
        permission_profile,
        env,
        LONG_TIMEOUT_MS,
        /*use_legacy_landlock*/ false,
    )
    .await
    .expect("sandbox should start with multiple denied files");

    assert_eq!(
        (output.exit_code, output.stdout.text, output.stderr.text),
        (
            0,
            "project instructions\nallowed\n".to_string(),
            String::new()
        )
    );
    assert_eq!(
        std::fs::read_to_string(workspace.join("allowed.txt")).expect("read allowed file"),
        "allowed\n"
    );
    for (path, contents) in &denied_files {
        assert_eq!(
            std::fs::read_to_string(path).expect("read host dummy file"),
            *contents
        );
    }
    if let Some(fixture) = rg_fixture {
        assert!(
            !fixture.path().join("rejected-rg-ran").exists(),
            "writable rg ran before confinement"
        );
        assert_eq!(
            fixture.path().join("protected-rg-ran").exists(),
            matches!(rules, DeniedFileRules::GlobsWithProtectedRg),
        );
    }
}
