use pretty_assertions::assert_eq;

use super::EnvironmentConfigLayer;
use super::EnvironmentConfigLayerFormat;
use super::EnvironmentConfigLayerStack;
use super::EnvironmentConfigReadParams;
use super::EnvironmentConfigReadResponse;
use crate::EnvironmentInfo;
use nova_executor_utils_path_uri::PathUri;

/// 稳定形状断言关注键名与嵌套结构；路径字面量随平台不同（windows 需要
/// 盘符绝对路径），URI 期望值从构造出的 PathUri 反推，保持跨平台一致。
fn fixture_roots() -> (std::path::PathBuf, std::path::PathBuf) {
    if cfg!(windows) {
        (
            std::path::PathBuf::from(r"C:\repo"),
            std::path::PathBuf::from(r"C:\home\u"),
        )
    } else {
        (
            std::path::PathBuf::from("/repo"),
            std::path::PathBuf::from("/home/u"),
        )
    }
}

#[test]
fn environment_config_read_params_use_stable_json_shape() {
    let (repo, _) = fixture_roots();
    let params = EnvironmentConfigReadParams {
        cwd: PathUri::from_host_native_path(&repo).expect("cwd URI"),
        config_paths: vec![
            vec!["sandbox".to_string()],
            vec!["network".to_string(), "mode".to_string()],
        ],
    };

    let expected = serde_json::json!({
        "cwd": params.cwd.to_string(),
        "configPaths": [["sandbox"], ["network", "mode"]],
    });
    assert_eq!(
        serde_json::to_value(&params).expect("serialize params"),
        expected
    );
    assert_eq!(
        serde_json::from_value::<EnvironmentConfigReadParams>(expected)
            .expect("deserialize params"),
        params
    );
}

#[test]
fn environment_config_read_response_uses_stable_json_shape() {
    let (repo, home) = fixture_roots();
    let user_home_uri = PathUri::from_host_native_path(&home).expect("home URI");
    let executor_home = home.join(".nova").join("executor");
    let project_settings = repo.join(".nova").join("settings.json");
    let response = EnvironmentConfigReadResponse {
        user_home_dir: Some(user_home_uri.clone()),
        executor_home_dir: PathUri::from_host_native_path(&executor_home)
            .expect("executor home URI"),
        hostname: Some("devbox".to_string()),
        config: EnvironmentConfigLayerStack {
            layers: vec![
                EnvironmentConfigLayer {
                    source: format!("user:{}", executor_home.join("config.toml").display()),
                    base_dir: PathUri::from_host_native_path(&executor_home)
                        .expect("user base URI"),
                    format: EnvironmentConfigLayerFormat::Toml,
                    content: "[sandbox]\nlevel = \"workspace-write\"\n".to_string(),
                    error: None,
                },
                EnvironmentConfigLayer {
                    source: format!("project:{}", project_settings.display()),
                    base_dir: PathUri::from_host_native_path(repo.join(".nova")).expect("base URI"),
                    format: EnvironmentConfigLayerFormat::Json,
                    content: String::new(),
                    error: Some(format!(
                        "failed to parse `{}`: ...",
                        project_settings.display()
                    )),
                },
            ],
            cloud_insertion_index: 2,
        },
    };

    let executor_base_uri = PathUri::from_host_native_path(&executor_home)
        .expect("user base URI")
        .to_string();
    let project_base_uri = PathUri::from_host_native_path(repo.join(".nova"))
        .expect("base URI")
        .to_string();
    let expected = serde_json::json!({
        "userHomeDir": user_home_uri.to_string(),
        "executorHomeDir": PathUri::from_host_native_path(&executor_home)
            .expect("executor home URI")
            .to_string(),
        "hostname": "devbox",
        "config": {
            "layers": [
                {
                    "source": format!("user:{}", executor_home.join("config.toml").display()),
                    "baseDir": executor_base_uri,
                    "format": "toml",
                    "content": "[sandbox]\nlevel = \"workspace-write\"\n",
                },
                {
                    "source": format!("project:{}", project_settings.display()),
                    "baseDir": project_base_uri,
                    "format": "json",
                    "content": "",
                    "error": format!("failed to parse `{}`: ...", project_settings.display()),
                },
            ],
            "cloudInsertionIndex": 2,
        },
    });
    assert_eq!(
        serde_json::to_value(&response).expect("serialize response"),
        expected
    );
    assert_eq!(
        serde_json::from_value::<EnvironmentConfigReadResponse>(expected)
            .expect("deserialize response"),
        response
    );
}

#[test]
fn environment_config_read_response_accepts_absent_optional_fields() {
    // 旧服务端/降级形态：可选字段（userHomeDir/hostname/error）缺省也能反序列化。
    let response: EnvironmentConfigReadResponse = serde_json::from_value(serde_json::json!({
        "executorHomeDir": "file:///home/u/.nova/executor",
        "config": {
            "layers": [
                {
                    "source": "user:/home/u/.nova/executor/config.toml",
                    "baseDir": "file:///home/u/.nova/executor",
                    "format": "toml",
                    "content": "",
                },
            ],
            "cloudInsertionIndex": 1,
        },
    }))
    .expect("response without optional fields should deserialize");

    assert_eq!(response.user_home_dir, None);
    assert_eq!(response.hostname, None);
    assert_eq!(response.config.layers[0].error, None);
}

#[test]
fn local_environment_info_advertises_environment_config_read() {
    // 能力位如实宣告：environmentConfig/read 已恢复（v1.4），客户端按位门控。
    assert!(
        EnvironmentInfo::local()
            .capabilities
            .environment_config_read
    );
}
