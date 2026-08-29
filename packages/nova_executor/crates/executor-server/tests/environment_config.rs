//! environmentConfig/read 端到端测试：真实服务器子进程 + 真实配置文件，
//! 验证注册表接线、两层读取与键路径投影（nova 语义）。

mod common;

use common::exec_server::exec_server;
use nova_executor_protocol::JSONRPCMessage;
use nova_executor_protocol::JSONRPCResponse;
use pretty_assertions::assert_eq;

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn exec_server_reads_environment_config_layers() -> anyhow::Result<()> {
    let mut server = exec_server().await?;
    // user 层：<executor home>/config.toml（harness 已把 NOVA_EXECUTOR_HOME
    // 指向临时目录）
    std::fs::write(
        server.nova_executor_home().join("config.toml"),
        r#"
[sandbox]
level = "workspace-write"

[network]
mode = "off"
"#,
    )?;
    // project 层：<cwd>/.nova/settings.json
    let project_root = tempfile::tempdir()?;
    let project_config_dir = project_root.path().join(".nova");
    std::fs::create_dir_all(&project_config_dir)?;
    std::fs::write(
        project_config_dir.join("settings.json"),
        r#"{"sandbox": {"level": "read-only"}, "model": {"provider": "drop-me"}}"#,
    )?;

    let initialize_id = server
        .send_request("initialize", serde_json::json!({"clientName": "test"}))
        .await?;
    let JSONRPCMessage::Response(JSONRPCResponse { id, .. }) = server.next_event().await? else {
        panic!("expected initialize response");
    };
    assert_eq!(id, initialize_id);
    server
        .send_notification("initialized", serde_json::json!({}))
        .await?;

    let cwd = nova_executor_utils_path_uri::PathUri::from_host_native_path(project_root.path())?;
    let read_id = server
        .send_request(
            "environmentConfig/read",
            serde_json::json!({
                "cwd": cwd.to_string(),
                "configPaths": [["sandbox"]],
            }),
        )
        .await?;
    let JSONRPCMessage::Response(JSONRPCResponse { id, result }) = server.next_event().await?
    else {
        panic!("expected environmentConfig/read response");
    };
    assert_eq!(id, read_id);

    // find_nova_executor_home 对 NOVA_EXECUTOR_HOME 做 canonicalize（macOS 上
    // /var → /private/var；windows 上会带 `\\?\` verbatim 前缀而服务端输出
    // 不带），断言期望值同样规整后再比对。
    #[allow(unused_mut)]
    let mut expected_home = server.nova_executor_home().canonicalize()?;
    #[cfg(windows)]
    {
        let text = expected_home.to_string_lossy();
        if let Some(stripped) = text.strip_prefix(r"\\?\") {
            expected_home = std::path::PathBuf::from(stripped);
        }
    }
    assert_eq!(
        result["executorHomeDir"],
        serde_json::json!(
            nova_executor_utils_path_uri::PathUri::from_host_native_path(&expected_home)?
                .to_string()
        )
    );
    let layers = result["config"]["layers"].as_array().expect("layers array");
    assert_eq!(layers.len(), 2);
    assert_eq!(
        result["config"]["cloudInsertionIndex"],
        serde_json::json!(2)
    );

    // user 层（低优先级在前）：TOML 投影回传
    let user = &layers[0];
    assert_eq!(
        user["source"],
        serde_json::json!(format!(
            "user:{}",
            expected_home.join("config.toml").display()
        ))
    );
    assert_eq!(user["format"], serde_json::json!("toml"));
    assert!(user.get("error").is_none());
    let user_toml: toml::Value =
        toml::from_str(user["content"].as_str().expect("user content string"))?;
    assert_eq!(
        user_toml,
        toml::from_str("[sandbox]\nlevel = \"workspace-write\"\n")?
    );

    // project 层（高优先级在后）：JSON 投影裁掉未选中的 model 键
    let project = &layers[1];
    assert_eq!(
        project["source"],
        serde_json::json!(format!(
            "project:{}",
            project_config_dir.join("settings.json").display()
        ))
    );
    assert_eq!(project["format"], serde_json::json!("json"));
    let project_json: serde_json::Value =
        serde_json::from_str(project["content"].as_str().expect("project content string"))?;
    assert_eq!(
        project_json,
        serde_json::json!({"sandbox": {"level": "read-only"}})
    );

    server.shutdown().await?;
    Ok(())
}
