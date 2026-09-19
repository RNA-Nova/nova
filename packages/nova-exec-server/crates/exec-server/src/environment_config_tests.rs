use std::path::Path;

use pretty_assertions::assert_eq;
use tempfile::tempdir;

use super::*;

fn str_paths(paths: &[&[&str]]) -> Vec<Vec<String>> {
    paths
        .iter()
        .map(|path| path.iter().map(|segment| segment.to_string()).collect())
        .collect()
}

fn params(project_root: &Path, config_paths: Vec<Vec<String>>) -> EnvironmentConfigReadParams {
    EnvironmentConfigReadParams {
        cwd: PathUri::from_host_native_path(project_root).expect("cwd URI"),
        config_paths,
    }
}

#[tokio::test]
async fn reads_user_and_project_layers_in_precedence_order() {
    let executor_home = tempdir().expect("executor home");
    let project_root = tempdir().expect("project root");
    std::fs::write(
        executor_home.path().join(USER_CONFIG_FILE_NAME),
        r#"
[sandbox]
level = "workspace-write"

[network]
mode = "off"
"#,
    )
    .expect("write user config");
    let project_config_dir = project_root.path().join(PROJECT_CONFIG_DIR_NAME);
    std::fs::create_dir_all(&project_config_dir).expect("create .nova dir");
    std::fs::write(
        project_config_dir.join(PROJECT_CONFIG_FILE_NAME),
        r#"{"sandbox": {"level": "read-only"}, "model": {"provider": "drop-me"}}"#,
    )
    .expect("write project settings");

    let response = read_environment_config_from(
        executor_home.path(),
        params(
            project_root.path(),
            str_paths(&[&["sandbox"], &["network"]]),
        ),
    )
    .await
    .expect("read environment config");

    assert_eq!(
        response.executor_home_dir,
        PathUri::from_abs_path(
            &AbsolutePathBuf::from_absolute_path(executor_home.path()).expect("absolute home")
        )
    );
    let stack = response.config;
    assert_eq!(stack.cloud_insertion_index, 2);
    assert_eq!(stack.layers.len(), 2);

    // user 层（低优先级，在前）：TOML 原样投影回传
    let user = &stack.layers[0];
    assert_eq!(
        user.source,
        format!(
            "user:{}",
            executor_home.path().join(USER_CONFIG_FILE_NAME).display()
        )
    );
    assert_eq!(
        user.base_dir,
        PathUri::from_host_native_path(executor_home.path()).expect("user base URI")
    );
    assert_eq!(user.format, EnvironmentConfigLayerFormat::Toml);
    assert_eq!(user.error, None);
    assert_eq!(
        toml::from_str::<toml::Value>(&user.content).expect("user layer TOML"),
        toml::from_str::<toml::Value>(
            r#"
[sandbox]
level = "workspace-write"

[network]
mode = "off"
"#
        )
        .expect("expected user TOML")
    );

    // project 层（高优先级，在后）：JSON 投影裁剪掉未选中的 model 键
    let project = &stack.layers[1];
    assert_eq!(
        project.source,
        format!(
            "project:{}",
            project_config_dir.join(PROJECT_CONFIG_FILE_NAME).display()
        )
    );
    assert_eq!(
        project.base_dir,
        PathUri::from_host_native_path(&project_config_dir).expect("project base URI")
    );
    assert_eq!(project.format, EnvironmentConfigLayerFormat::Json);
    assert_eq!(project.error, None);
    assert_eq!(
        serde_json::from_str::<serde_json::Value>(&project.content).expect("project layer JSON"),
        serde_json::json!({"sandbox": {"level": "read-only"}})
    );
}

#[tokio::test]
async fn missing_files_yield_empty_layers_without_error() {
    let executor_home = tempdir().expect("executor home");
    let project_root = tempdir().expect("project root");

    let response = read_environment_config_from(
        executor_home.path(),
        params(project_root.path(), str_paths(&[&["sandbox"]])),
    )
    .await
    .expect("read environment config");

    let stack = response.config;
    assert_eq!(stack.layers.len(), 2);
    // 缺文件 = 空层（各格式的空文档形态），不回错
    assert_eq!(stack.layers[0].content, "");
    assert_eq!(stack.layers[0].error, None);
    assert_eq!(stack.layers[1].content, "{}");
    assert_eq!(stack.layers[1].error, None);
}

#[tokio::test]
async fn projection_trims_to_selected_key_paths() {
    let executor_home = tempdir().expect("executor home");
    let project_root = tempdir().expect("project root");
    std::fs::write(
        executor_home.path().join(USER_CONFIG_FILE_NAME),
        r#"
[sandbox]
level = "full"

[network]
mode = "off"
allow_domains = ["example.com"]

other = "drop-me"
"#,
    )
    .expect("write user config");
    let project_config_dir = project_root.path().join(PROJECT_CONFIG_DIR_NAME);
    std::fs::create_dir_all(&project_config_dir).expect("create .nova dir");
    std::fs::write(
        project_config_dir.join(PROJECT_CONFIG_FILE_NAME),
        r#"{"network": {"mode": "full", "allowDomains": ["a.com"]}, "other": "drop-me"}"#,
    )
    .expect("write project settings");

    let response = read_environment_config_from(
        executor_home.path(),
        params(project_root.path(), str_paths(&[&["network", "mode"]])),
    )
    .await
    .expect("read environment config");

    assert_eq!(
        toml::from_str::<toml::Value>(&response.config.layers[0].content).expect("user layer TOML"),
        toml::from_str::<toml::Value>(
            r#"
[network]
mode = "off"
"#
        )
        .expect("expected user TOML")
    );
    assert_eq!(
        serde_json::from_str::<serde_json::Value>(&response.config.layers[1].content)
            .expect("project layer JSON"),
        serde_json::json!({"network": {"mode": "full"}})
    );
}

#[tokio::test]
async fn projection_preserves_non_table_ancestors() {
    // 选择器深入标量之下时，非表/非对象祖先原样保留（使其仍可覆盖更低层——
    // 照 codex 语义），不因子键不匹配而被裁掉。
    let executor_home = tempdir().expect("executor home");
    let project_root = tempdir().expect("project root");
    std::fs::write(
        executor_home.path().join(USER_CONFIG_FILE_NAME),
        r#"sandbox = "full""#,
    )
    .expect("write user config");
    let project_config_dir = project_root.path().join(PROJECT_CONFIG_DIR_NAME);
    std::fs::create_dir_all(&project_config_dir).expect("create .nova dir");
    std::fs::write(
        project_config_dir.join(PROJECT_CONFIG_FILE_NAME),
        r#"{"sandbox": "read-only"}"#,
    )
    .expect("write project settings");

    let response = read_environment_config_from(
        executor_home.path(),
        params(project_root.path(), str_paths(&[&["sandbox", "level"]])),
    )
    .await
    .expect("read environment config");

    assert_eq!(
        toml::from_str::<toml::Value>(&response.config.layers[0].content).expect("user layer TOML"),
        toml::from_str::<toml::Value>(r#"sandbox = "full""#).expect("expected user TOML")
    );
    assert_eq!(
        serde_json::from_str::<serde_json::Value>(&response.config.layers[1].content)
            .expect("project layer JSON"),
        serde_json::json!({"sandbox": "read-only"})
    );
}

#[tokio::test]
async fn parse_error_sets_layer_error_without_failing_call() {
    let executor_home = tempdir().expect("executor home");
    let project_root = tempdir().expect("project root");
    std::fs::write(
        executor_home.path().join(USER_CONFIG_FILE_NAME),
        "not [valid toml",
    )
    .expect("write broken user config");
    let project_config_dir = project_root.path().join(PROJECT_CONFIG_DIR_NAME);
    std::fs::create_dir_all(&project_config_dir).expect("create .nova dir");
    std::fs::write(
        project_config_dir.join(PROJECT_CONFIG_FILE_NAME),
        r#"{"sandbox": {"level": "read-only"}}"#,
    )
    .expect("write project settings");

    let response = read_environment_config_from(
        executor_home.path(),
        params(project_root.path(), str_paths(&[&["sandbox"]])),
    )
    .await
    .expect("parse failure must not fail the whole call");

    // 解析失败层：content 为空、error 字段带回；其余层不受影响
    let user = &response.config.layers[0];
    assert_eq!(user.content, "");
    assert!(
        user.error
            .as_deref()
            .is_some_and(|error| error.contains("failed to parse")),
        "expected parse error on user layer, got {:?}",
        user.error
    );
    let project = &response.config.layers[1];
    assert_eq!(project.error, None);
    assert_eq!(
        serde_json::from_str::<serde_json::Value>(&project.content).expect("project layer JSON"),
        serde_json::json!({"sandbox": {"level": "read-only"}})
    );
}

#[tokio::test]
async fn rejects_invalid_params() {
    let executor_home = tempdir().expect("executor home");
    let project_root = tempdir().expect("project root");

    let error = read_environment_config_from(
        executor_home.path(),
        params(project_root.path(), Vec::new()),
    )
    .await
    .expect_err("empty config paths should fail");
    assert_eq!(error.to_string(), "at least one config path is required");

    let error = read_environment_config_from(
        executor_home.path(),
        params(project_root.path(), str_paths(&[&["sandbox"], &[]])),
    )
    .await
    .expect_err("empty path should fail");
    assert_eq!(
        error.to_string(),
        "config paths must contain at least one key segment"
    );
}
