use pretty_assertions::assert_eq;

use super::EnvironmentConfigLayer;
use super::EnvironmentConfigLayerFormat;
use super::EnvironmentConfigLayerStack;
use super::EnvironmentConfigReadParams;
use super::EnvironmentConfigReadResponse;
use crate::EnvironmentInfo;
use nova_executor_utils_path_uri::PathUri;

#[test]
fn environment_config_read_params_use_stable_json_shape() {
    let params = EnvironmentConfigReadParams {
        cwd: PathUri::from_host_native_path("/repo").expect("cwd URI"),
        config_paths: vec![
            vec!["sandbox".to_string()],
            vec!["network".to_string(), "mode".to_string()],
        ],
    };

    let expected = serde_json::json!({
        "cwd": "file:///repo",
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
    let response = EnvironmentConfigReadResponse {
        user_home_dir: Some(PathUri::from_host_native_path("/home/u").expect("home URI")),
        executor_home_dir: PathUri::from_host_native_path("/home/u/.nova/executor")
            .expect("executor home URI"),
        hostname: Some("devbox".to_string()),
        config: EnvironmentConfigLayerStack {
            layers: vec![
                EnvironmentConfigLayer {
                    source: "user:/home/u/.nova/executor/config.toml".to_string(),
                    base_dir: PathUri::from_host_native_path("/home/u/.nova/executor")
                        .expect("user base URI"),
                    format: EnvironmentConfigLayerFormat::Toml,
                    content: "[sandbox]\nlevel = \"workspace-write\"\n".to_string(),
                    error: None,
                },
                EnvironmentConfigLayer {
                    source: "project:/repo/.nova/settings.json".to_string(),
                    base_dir: PathUri::from_host_native_path("/repo/.nova").expect("base URI"),
                    format: EnvironmentConfigLayerFormat::Json,
                    content: String::new(),
                    error: Some("failed to parse `/repo/.nova/settings.json`: ...".to_string()),
                },
            ],
            cloud_insertion_index: 2,
        },
    };

    let expected = serde_json::json!({
        "userHomeDir": "file:///home/u",
        "executorHomeDir": "file:///home/u/.nova/executor",
        "hostname": "devbox",
        "config": {
            "layers": [
                {
                    "source": "user:/home/u/.nova/executor/config.toml",
                    "baseDir": "file:///home/u/.nova/executor",
                    "format": "toml",
                    "content": "[sandbox]\nlevel = \"workspace-write\"\n",
                },
                {
                    "source": "project:/repo/.nova/settings.json",
                    "baseDir": "file:///repo/.nova",
                    "format": "json",
                    "content": "",
                    "error": "failed to parse `/repo/.nova/settings.json`: ...",
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
