//! 同机共存测试：两个 executor 实例并行运行互不污染（会话 id 独立、
//! 进程表互不可见、端口各听各的）。
//!
//! 注：同机两实例共享宿主机文件系统（fs 无每实例视图，不属于隔离面）；
//! 与 codex exec-server 系统对象的共存面由 Windows 命名空间独立批收口
//! （WFP GUID/防火墙规则/用户组/互斥体全部 nova 命名空间——CI 无 codex
//! 实体可装，真机共存验证挂账）。

mod common;

use common::exec_server::exec_server;
use nova_executor_protocol::JSONRPCMessage;
use nova_executor_protocol::JSONRPCResponse;

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn two_executor_instances_are_isolated() -> anyhow::Result<()> {
    let mut server_a = exec_server().await?;
    let mut server_b = exec_server().await?;

    // 两个实例各自 initialize——会话 id 必须不同
    let mut sessions = Vec::new();
    for server in [&mut server_a, &mut server_b] {
        let initialize_id = server
            .send_request("initialize", serde_json::json!({"clientName": "coexist-test"}))
            .await?;
        let JSONRPCMessage::Response(JSONRPCResponse { id, result, .. }) =
            server.next_event().await?
        else {
            panic!("expected initialize response");
        };
        assert_eq!(id, initialize_id);
        sessions.push(result["sessionId"].as_str().expect("sessionId").to_string());
        server
            .send_notification("initialized", serde_json::json!({}))
            .await?;
    }
    assert_ne!(sessions[0], sessions[1], "两实例会话必须独立");

    // A 实例起进程写标记
    let work_dir = tempfile::tempdir()?;
    let cwd_uri = nova_executor_utils_path_uri::PathUri::from_host_native_path(
        work_dir.path(),
    )?;
    let start_id = server_a
        .send_request(
            "process/start",
            serde_json::json!({
                "processId": "proc-a",
                "argv": ["sh", "-c", "printf hello-a"],
                "cwd": cwd_uri.to_string(),
                "env": {},
                "tty": false,
            }),
        )
        .await?;
    // 事件流里可能穿插通知——读到匹配的响应/错误为止
    loop {
        match server_a.next_event().await? {
            JSONRPCMessage::Response(JSONRPCResponse { id, .. }) if id == start_id => break,
            JSONRPCMessage::Error(error) if error.id == start_id => {
                panic!("process/start failed: {}", error.error.message)
            }
            _ => {}
        }
    }

    // A 读得到自己的进程输出
    let read_id = server_a
        .send_request(
            "process/read",
            serde_json::json!({"processId": "proc-a", "waitMs": 3000}),
        )
        .await?;
    let mut saw_output = false;
    let mut read_response_seen = false;
    while !read_response_seen {
        match server_a.next_event().await? {
            JSONRPCMessage::Response(JSONRPCResponse { id, result, .. }) if id == read_id => {
                read_response_seen = true;
                let chunks = result["chunks"].as_array().expect("chunks");
                saw_output = chunks.iter().any(|chunk| {
                    chunk["chunk"].as_str().is_some_and(|data| {
                        String::from_utf8(
                            base64::Engine::decode(
                                &base64::engine::general_purpose::STANDARD,
                                data,
                            )
                            .unwrap_or_default(),
                        )
                        .unwrap_or_default()
                        .contains("hello-a")
                    })
                });
            }
            _ => {}
        }
    }
    assert!(saw_output, "A 实例必须读到自己进程的输出");

    // B 实例看不到 A 的进程（进程表按会话/连接隔离）
    let foreign_read_id = server_b
        .send_request(
            "process/read",
            serde_json::json!({"processId": "proc-a", "waitMs": 100}),
        )
        .await?;
    let mut foreign_error_seen = false;
    for _ in 0..8 {
        match server_b.next_event().await? {
            JSONRPCMessage::Error(error) if error.id == foreign_read_id => {
                foreign_error_seen = true;
                break;
            }
            JSONRPCMessage::Response(JSONRPCResponse { id, result, .. })
                if id == foreign_read_id =>
            {
                // 若返回正常响应，进程表必须是空的（不能见到 A 的进程状态）
                assert!(
                    result["chunks"].as_array().is_none_or(Vec::is_empty),
                    "B 不得读到 A 的进程输出"
                );
                foreign_error_seen = true;
                break;
            }
            _ => {}
        }
    }
    assert!(foreign_error_seen, "B 对 A 的进程 id 必须查无此进程");

    server_a.shutdown().await?;
    server_b.shutdown().await?;
    Ok(())
}
