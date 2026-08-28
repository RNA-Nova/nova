//! environmentConfig/read 协议类型族（v1.4 恢复，机制照 codex exec-server，
//! 内容换 nova 体系）。
//!
//! nova 语义：executor 是"代读的手"——客户端够不到远程机器的盘，executor
//! 读自己所在机器的配置层、按键路径投影后如实回传层栈。**不合并不裁决**：
//! 层合并与 trust 裁决归客户端（nova 的 trust 体系在客户端，executor 不做
//! 门控）。
//!
//! nova 的配置层（代替 codex 的 system/user/project codex.toml 层栈）：
//! - user 层：`~/.nova/executor/config.toml`（TOML——executor 自有环境配置）
//! - project 层：`<cwd>/.nova/settings.json`（JSON，与 nova 体系项目级配置一致）

use nova_executor_utils_path_uri::PathUri;
use serde::Deserialize;
use serde::Serialize;

pub const ENVIRONMENT_CONFIG_READ_METHOD: &str = "environmentConfig/read";

/// 按字面键路径选择 executor 本机配置字段。
///
/// 每条路径是一串键段（如 `["sandbox", "level"]`），多条路径汇成前缀树做
/// 投影；至少一条路径、每条路径至少一个键段，否则服务端拒为 invalid_params
/// （不允许整文档读取——RPC 边界照 codex 纪律收紧）。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EnvironmentConfigReadParams {
    /// 项目工作目录（定位 project 层 `<cwd>/.nova/settings.json`）。
    pub cwd: PathUri,
    pub config_paths: Vec<Vec<String>>,
}

/// executor 本机配置层栈与环境信息。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EnvironmentConfigReadResponse {
    /// executor 用户家目录（客户端展开路径值中 `~` 的目标）。
    pub user_home_dir: Option<PathUri>,
    /// executor 家目录（`~/.nova/executor`，或 `NOVA_EXECUTOR_HOME` 覆盖）——
    /// user 层配置所在目录。
    pub executor_home_dir: PathUri,
    /// executor 主机名（诊断用途；客户端不得据此推导行为）。
    pub hostname: Option<String>,
    pub config: EnvironmentConfigLayerStack,
}

/// 一组有序的已选配置层。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EnvironmentConfigLayerStack {
    /// 从低到高优先级排序（user 层在前、project 层在后）。两层恒在——文件
    /// 缺失或投影为空时以空 content 层占位，不从栈中剔除（nova 与 codex 的
    /// 分歧点：codex 剔除空层，nova 固定两层栈保序，方便客户端按位合并）。
    pub layers: Vec<EnvironmentConfigLayer>,
    /// 预留对位字段（云托管层插入位）。nova 当前无云配置层，恒等于
    /// `layers.len()`（将来若引入，追加末尾即最高优先级）。
    pub cloud_insertion_index: usize,
}

/// 一个已选的 executor 本机配置层。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EnvironmentConfigLayer {
    /// 层来源标记（不透明诊断串，形如 `user:<绝对路径>` / `project:<绝对路径>`；
    /// 调用方不得据此推导行为）。
    pub source: String,
    /// 解释层内相对路径的基准目录（user 层 = executor home；
    /// project 层 = `<cwd>/.nova`）。
    pub base_dir: PathUri,
    /// 层内容格式——客户端按格式解析 `content`。
    pub format: EnvironmentConfigLayerFormat,
    /// 投影后的层内容原文（该格式下的合法文档；空层为其空文档形态——
    /// TOML 为 `""`，JSON 为 `"{}"`）。路径值未经归一化。
    pub content: String,
    /// 层读取/解析错误。**文件缺失不算错误**（空层）；TOML/JSON 解析失败或
    /// 读取 IO 失败时 content 为空、错误信息回本字段——整个调用不因此失败。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// 配置层内容格式（user 层 TOML / project 层 JSON）。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum EnvironmentConfigLayerFormat {
    Toml,
    Json,
}

#[cfg(test)]
#[path = "environment_config_tests.rs"]
mod tests;
