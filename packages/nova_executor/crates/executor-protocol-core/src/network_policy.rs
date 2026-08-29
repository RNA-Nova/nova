use nova_executor_network_proxy::NetworkDecisionSource;
use nova_executor_network_proxy::NetworkPolicyDecision;
use schemars::JsonSchema;
use serde::Deserialize;
use serde::Serialize;
use ts_rs::TS;

/// 网络审批/放行的协议类别（自 approvals 面内联——执行面仅需此枚举）。
#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq, JsonSchema, TS)]
#[serde(rename_all = "snake_case")]
pub enum NetworkApprovalProtocol {
    Http,
    #[serde(alias = "https_connect", alias = "http-connect")]
    Https,
    Socks5Tcp,
    Socks5Udp,
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct NetworkPolicyDecisionPayload {
    pub decision: NetworkPolicyDecision,
    pub source: NetworkDecisionSource,
    #[serde(default)]
    pub protocol: Option<NetworkApprovalProtocol>,
    pub host: Option<String>,
    pub reason: Option<String>,
    pub port: Option<u16>,
}

impl NetworkPolicyDecisionPayload {
    pub fn is_ask_from_decider(&self) -> bool {
        self.decision == NetworkPolicyDecision::Ask && self.source == NetworkDecisionSource::Decider
    }
}
