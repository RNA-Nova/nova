//! environmentConfig/read 服务端实现：executor 代读本机配置层栈回传客户端。
//!
//! executor 是"代读的手"——客户端够不到远程机器的盘，executor 读自己所在
//! 机器的配置层、按键路径投影后如实回传；**不合并不裁决**（合并与 trust
//! 裁决归客户端）。层栈固定两层、从低到高优先级：
//!
//! - user 层：`<executor home>/config.toml`（TOML，executor 自有环境配置）
//! - project 层：`<cwd>/.nova/settings.json`（JSON，nova 体系项目级配置）
//!
//! 容错纪律：文件缺失 = 空层不回错；TOML/JSON 解析失败或读取 IO 失败 =
//! 该层 error 字段带回（content 为空），不炸整个调用。

use std::collections::BTreeMap;
use std::path::Path;

use nova_executor_protocol::EnvironmentConfigLayer;
use nova_executor_protocol::EnvironmentConfigLayerFormat;
use nova_executor_protocol::EnvironmentConfigLayerStack;
use nova_executor_protocol::EnvironmentConfigReadParams;
use nova_executor_protocol::EnvironmentConfigReadResponse;
use nova_executor_utils_absolute_path::AbsolutePathBuf;
use nova_executor_utils_home_dir::find_nova_executor_home;
use nova_executor_utils_path_uri::PathUri;

/// user 层配置文件名（位于 executor home 下）
const USER_CONFIG_FILE_NAME: &str = "config.toml";
/// project 层配置位置（与 nova 体系项目级配置目录一致）
const PROJECT_CONFIG_DIR_NAME: &str = ".nova";
const PROJECT_CONFIG_FILE_NAME: &str = "settings.json";

#[derive(Debug, thiserror::Error)]
pub(crate) enum ReadEnvironmentConfigError {
    #[error("{0}")]
    InvalidParams(String),
    #[error("{0}")]
    Internal(String),
}

pub(crate) async fn read_environment_config(
    params: EnvironmentConfigReadParams,
) -> Result<EnvironmentConfigReadResponse, ReadEnvironmentConfigError> {
    let executor_home = find_nova_executor_home().map_err(|error| {
        ReadEnvironmentConfigError::Internal(format!("failed to find executor home: {error}"))
    })?;
    read_environment_config_from(executor_home.as_path(), params).await
}

/// 可测试内核：executor home 显式注入（测试喂 tempdir，不碰进程环境变量）。
async fn read_environment_config_from(
    executor_home: &Path,
    params: EnvironmentConfigReadParams,
) -> Result<EnvironmentConfigReadResponse, ReadEnvironmentConfigError> {
    validate_params(&params)?;
    let cwd = params
        .cwd
        .to_abs_path()
        .map_err(|error| ReadEnvironmentConfigError::InvalidParams(error.to_string()))?;
    let executor_home = AbsolutePathBuf::from_absolute_path(executor_home).map_err(|error| {
        ReadEnvironmentConfigError::Internal(format!("executor home is not absolute: {error}"))
    })?;

    let selectors = SelectorNode::from_paths(&params.config_paths);

    // user 层：<executor home>/config.toml（TOML），base_dir = executor home
    let user_file = executor_home.join(USER_CONFIG_FILE_NAME);
    let user_layer = match read_layer_text(user_file.as_path()).await {
        LayerRead::Missing => environment_layer(
            "user",
            user_file.as_path(),
            executor_home.clone(),
            EnvironmentConfigLayerFormat::Toml,
            // 缺文件 = 空层（TOML 空文档为空串）
            String::new(),
            None,
        ),
        LayerRead::Failed(error) => environment_layer(
            "user",
            user_file.as_path(),
            executor_home.clone(),
            EnvironmentConfigLayerFormat::Toml,
            String::new(),
            Some(error),
        ),
        LayerRead::Text(text) => match toml::from_str::<toml::Value>(&text) {
            Ok(document) => {
                let projected = project_toml(&document, &selectors);
                let content = toml::to_string(&projected).map_err(|error| {
                    ReadEnvironmentConfigError::Internal(format!(
                        "failed to serialize executor-local config: {error}"
                    ))
                })?;
                environment_layer(
                    "user",
                    user_file.as_path(),
                    executor_home.clone(),
                    EnvironmentConfigLayerFormat::Toml,
                    content,
                    None,
                )
            }
            Err(error) => environment_layer(
                "user",
                user_file.as_path(),
                executor_home.clone(),
                EnvironmentConfigLayerFormat::Toml,
                String::new(),
                Some(format!(
                    "failed to parse `{}`: {error}",
                    user_file.as_path().display()
                )),
            ),
        },
    };

    // project 层：<cwd>/.nova/settings.json（JSON），base_dir = <cwd>/.nova
    //（nova 项目级资源以 `<cwd>/.nova` 为 base——路径值相对它解释）
    let project_dir = cwd.join(PROJECT_CONFIG_DIR_NAME);
    let project_file = project_dir.join(PROJECT_CONFIG_FILE_NAME);
    let project_layer = match read_layer_text(project_file.as_path()).await {
        LayerRead::Missing => environment_layer(
            "project",
            project_file.as_path(),
            project_dir.clone(),
            EnvironmentConfigLayerFormat::Json,
            // 缺文件 = 空层（JSON 空文档为空对象）
            "{}".to_string(),
            None,
        ),
        LayerRead::Failed(error) => environment_layer(
            "project",
            project_file.as_path(),
            project_dir.clone(),
            EnvironmentConfigLayerFormat::Json,
            String::new(),
            Some(error),
        ),
        LayerRead::Text(text) => match serde_json::from_str::<serde_json::Value>(&text) {
            Ok(document) => {
                let projected = project_json(&document, &selectors);
                let content = serde_json::to_string_pretty(&projected).map_err(|error| {
                    ReadEnvironmentConfigError::Internal(format!(
                        "failed to serialize executor-local config: {error}"
                    ))
                })?;
                environment_layer(
                    "project",
                    project_file.as_path(),
                    project_dir.clone(),
                    EnvironmentConfigLayerFormat::Json,
                    content,
                    None,
                )
            }
            Err(error) => environment_layer(
                "project",
                project_file.as_path(),
                project_dir.clone(),
                EnvironmentConfigLayerFormat::Json,
                String::new(),
                Some(format!(
                    "failed to parse `{}`: {error}",
                    project_file.as_path().display()
                )),
            ),
        },
    };

    let layers = vec![user_layer, project_layer];
    Ok(EnvironmentConfigReadResponse {
        // 家目录经 `~` 展开取绝对路径——与 EnvironmentInfo::local() 同一语义
        user_home_dir: PathUri::from_host_native_path("~").ok(),
        executor_home_dir: PathUri::from_abs_path(&executor_home),
        hostname: host_name(),
        config: EnvironmentConfigLayerStack {
            cloud_insertion_index: layers.len(),
            layers,
        },
    })
}

fn validate_params(params: &EnvironmentConfigReadParams) -> Result<(), ReadEnvironmentConfigError> {
    if params.config_paths.is_empty() {
        return Err(ReadEnvironmentConfigError::InvalidParams(
            "at least one config path is required".to_string(),
        ));
    }
    if params.config_paths.iter().any(Vec::is_empty) {
        return Err(ReadEnvironmentConfigError::InvalidParams(
            "config paths must contain at least one key segment".to_string(),
        ));
    }
    Ok(())
}

fn environment_layer(
    kind: &str,
    file: &Path,
    base_dir: AbsolutePathBuf,
    format: EnvironmentConfigLayerFormat,
    content: String,
    error: Option<String>,
) -> EnvironmentConfigLayer {
    EnvironmentConfigLayer {
        // nova 语义的层源标记（不透明诊断串）：`<层名>:<绝对路径>`
        source: format!("{kind}:{}", file.display()),
        base_dir: PathUri::from_abs_path(&base_dir),
        format,
        content,
        error,
    }
}

/// 层文本读取三态：缺失（空层）/ 成功 / 失败（层 error 字段带回，不炸调用）。
enum LayerRead {
    Missing,
    Text(String),
    Failed(String),
}

async fn read_layer_text(path: &Path) -> LayerRead {
    match tokio::fs::read_to_string(path).await {
        Ok(text) => LayerRead::Text(text),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => LayerRead::Missing,
        Err(error) => LayerRead::Failed(format!("failed to read `{}`: {error}", path.display())),
    }
}

fn host_name() -> Option<String> {
    let hostname = gethostname::gethostname()
        .to_string_lossy()
        .trim()
        .to_string();
    (!hostname.is_empty()).then_some(hostname)
}

/// 键路径选择器前缀树（照 codex config/loader 的 SelectorNode 裁剪语义）：
/// 多条键路径汇成一棵树，terminal 节点选中整个子树。
#[derive(Default)]
struct SelectorNode {
    terminal: bool,
    children: BTreeMap<String, SelectorNode>,
}

impl SelectorNode {
    fn from_paths(paths: &[Vec<String>]) -> Self {
        let mut root = Self::default();
        for path in paths {
            root.insert(path);
        }
        root
    }

    fn insert(&mut self, path: &[String]) {
        if self.terminal {
            return;
        }
        let Some((segment, remaining)) = path.split_first() else {
            self.terminal = true;
            self.children.clear();
            return;
        };
        self.children
            .entry(segment.clone())
            .or_default()
            .insert(remaining);
    }
}

/// TOML 投影：未命中的键剔除；terminal 选中整个子树；非表祖先原样保留
///（使其仍可覆盖更低层——照 codex 语义）。投影为空不剔除层（nova 固定
/// 两层栈保序，与 codex 剔空层的分歧点）。
fn project_toml(value: &toml::Value, selector: &SelectorNode) -> toml::Value {
    if selector.terminal {
        return value.clone();
    }
    let Some(table) = value.as_table() else {
        return value.clone();
    };
    let mut projected = toml::map::Map::new();
    for (key, value) in table {
        let Some(child_selector) = selector.children.get(key) else {
            continue;
        };
        projected.insert(key.clone(), project_toml(value, child_selector));
    }
    toml::Value::Table(projected)
}

/// JSON 投影：与 TOML 同语义（对象 ↔ 表对位）。
fn project_json(value: &serde_json::Value, selector: &SelectorNode) -> serde_json::Value {
    if selector.terminal {
        return value.clone();
    }
    let Some(object) = value.as_object() else {
        return value.clone();
    };
    let mut projected = serde_json::Map::new();
    for (key, value) in object {
        let Some(child_selector) = selector.children.get(key) else {
            continue;
        };
        projected.insert(key.clone(), project_json(value, child_selector));
    }
    serde_json::Value::Object(projected)
}

#[cfg(test)]
#[path = "environment_config_tests.rs"]
mod tests;
