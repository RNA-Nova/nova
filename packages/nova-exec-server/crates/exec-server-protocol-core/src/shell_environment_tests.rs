use std::collections::HashMap;
use std::process::Command;

use pretty_assertions::assert_eq;

use super::*;

const CHILD_MODE_ENV_VAR: &str = "NOVA_EXEC_SERVER_SHELL_ENVIRONMENT_SCRUBBER_TEST_MODE";
const TEST_NAME: &str =
    "shell_environment::tests::command_scrubber_removes_names_from_real_child_environment";

// 对位 codex 9b738582b1：工具调用 ID 的写入/清理语义（含平台同名变量）
#[test]
fn tool_call_id_replaces_platform_equivalent_names() {
    let representable = format!("exec-雪-{}", "x".repeat(512));
    for (call_id, expected_call) in [
        (None, None),
        (Some(""), Some("")),
        (Some("invalid\0call"), None),
        (Some("current-call"), Some("current-call")),
        (Some(representable.as_str()), Some(representable.as_str())),
    ] {
        let mut env = HashMap::from([
            ("CODEX_TOOL_CALL_ID".to_string(), "stale-call".to_string()),
            (
                "Codex_Tool_Call_Id".to_string(),
                "mixed-case-call".to_string(),
            ),
            (
                "codex_tool_call_id".to_string(),
                "lowercase-call".to_string(),
            ),
            ("OTHER".to_string(), "unchanged".to_string()),
        ]);

        set_tool_call_id_env_var(&mut env, call_id);

        let mut expected = HashMap::from([("OTHER".to_string(), "unchanged".to_string())]);
        #[cfg(not(windows))]
        expected.extend([
            (
                "Codex_Tool_Call_Id".to_string(),
                "mixed-case-call".to_string(),
            ),
            (
                "codex_tool_call_id".to_string(),
                "lowercase-call".to_string(),
            ),
        ]);
        if let Some(expected_call) = expected_call {
            expected.insert("CODEX_TOOL_CALL_ID".to_string(), expected_call.to_string());
        }
        assert_eq!(env, expected, "call_id: {call_id:?}");
    }
}

#[test]
fn non_inheritable_environment_is_removed_after_policy_overrides() {
    let vars = [
        ("SAFE".to_string(), "inherited".to_string()),
        (
            "openai_federation_rule_id".to_string(),
            "inherited-rule".to_string(),
        ),
    ];
    let policy = ShellEnvironmentPolicy {
        inherit: ShellEnvironmentPolicyInherit::All,
        ignore_default_excludes: true,
        r#set: HashMap::from([
            ("SAFE".to_string(), "override".to_string()),
            (
                "OpenAI_Identity_Token_File".to_string(),
                "/run/identity-token".to_string(),
            ),
        ]),
        ..Default::default()
    };

    assert_eq!(
        populate_env(vars, &policy),
        HashMap::from([("SAFE".to_string(), "override".to_string())])
    );
}

#[test]
fn command_scrubber_removes_names_from_real_child_environment() {
    if std::env::var_os(CHILD_MODE_ENV_VAR).is_none() {
        let output = Command::new(std::env::current_exe().expect("locate current test binary"))
            .args([TEST_NAME, "--exact", "--nocapture"])
            .env(CHILD_MODE_ENV_VAR, "1")
            .env("OpenAI_Federation_Rule_Id", "inherited-rule")
            .output()
            .expect("run inherited-environment test process");
        assert!(
            output.status.success(),
            "child failed\nstdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr),
        );
        return;
    }

    let mut command = environment_command();
    command
        .env("openai_identity_token_file", "/run/identity-token")
        .env("SAFE", "value");
    scrub_non_inheritable_env_vars(&mut command);
    let output = command.output().expect("read child environment");
    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    let restricted_names = stdout
        .lines()
        .filter_map(|line| line.split_once('=').map(|(name, _)| name))
        .filter(|name| is_non_inheritable_env_var(name))
        .collect::<Vec<_>>();
    assert_eq!(restricted_names, Vec::<&str>::new());
}

#[cfg(windows)]
fn environment_command() -> Command {
    let mut command = Command::new("cmd.exe");
    command.args(["/D", "/C", "set"]);
    command
}

#[cfg(not(windows))]
fn environment_command() -> Command {
    Command::new("env")
}
