//! 代理拒绝响应构造（移植自 codex network-proxy `responses.rs`）。
//!
//! 裁点：MITM 面第一期未移植，`REASON_MITM_HOOK_DENIED` / `REASON_MITM_REQUIRED`
//! 的映射分支随之裁掉；`text_response` / `blocked_text_response` 两个仅被 MITM
//! 路径消费的构造器也一并裁掉（恢复 MITM 时从上游找回）。

use crate::NetworkDecisionSource;
use crate::NetworkPolicyDecision;
use crate::NetworkProtocol;
use crate::reasons::REASON_DENIED;
use crate::reasons::REASON_METHOD_NOT_ALLOWED;
use crate::reasons::REASON_NOT_ALLOWED;
use crate::reasons::REASON_NOT_ALLOWED_LOCAL;
use crate::reasons::REASON_PROXY_DISABLED;
use rama_http::Body;
use rama_http::Response;
use rama_http::StatusCode;
use serde::Serialize;
use tracing::error;

pub struct PolicyDecisionDetails<'a> {
    pub decision: NetworkPolicyDecision,
    pub reason: &'a str,
    pub source: NetworkDecisionSource,
    pub protocol: NetworkProtocol,
    pub host: &'a str,
    pub port: u16,
}

pub fn json_response<T: Serialize>(value: &T) -> Response {
    let body = match serde_json::to_string(value) {
        Ok(body) => body,
        Err(err) => {
            error!("failed to serialize JSON response: {err}");
            "{}".to_string()
        }
    };
    Response::builder()
        .status(StatusCode::OK)
        .header("content-type", "application/json")
        .body(Body::from(body))
        .unwrap_or_else(|err| {
            error!("failed to build JSON response: {err}");
            Response::new(Body::from("{}"))
        })
}

pub fn blocked_header_value(reason: &str) -> &'static str {
    match reason {
        REASON_NOT_ALLOWED | REASON_NOT_ALLOWED_LOCAL => "blocked-by-allowlist",
        REASON_DENIED => "blocked-by-denylist",
        REASON_METHOD_NOT_ALLOWED => "blocked-by-method-policy",
        _ => "blocked-by-policy",
    }
}

pub fn blocked_message(reason: &str) -> &'static str {
    match reason {
        REASON_NOT_ALLOWED => "Domain not in allowlist.",
        REASON_NOT_ALLOWED_LOCAL => "Sandbox policy blocks local/private network addresses.",
        REASON_DENIED => "Domain denied by the sandbox policy.",
        // 适配：nova 线上 NetworkMode 没有 limited 变体，None = 无网络访问。
        REASON_METHOD_NOT_ALLOWED => "Method not allowed by the current network mode.",
        REASON_PROXY_DISABLED => "network proxy is disabled",
        _ => "Request blocked by network policy.",
    }
}

pub fn blocked_message_with_policy(reason: &str, details: &PolicyDecisionDetails<'_>) -> String {
    let _ = (details.reason, details.host);
    blocked_message(reason).to_string()
}

pub fn blocked_text_response_with_policy(
    reason: &str,
    details: &PolicyDecisionDetails<'_>,
) -> Response {
    Response::builder()
        .status(StatusCode::FORBIDDEN)
        .header("content-type", "text/plain")
        .header("x-proxy-error", blocked_header_value(reason))
        .body(Body::from(blocked_message_with_policy(reason, details)))
        .unwrap_or_else(|_| Response::new(Body::from("blocked")))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::reasons::REASON_NOT_ALLOWED;
    use pretty_assertions::assert_eq;

    #[test]
    fn blocked_message_with_policy_returns_human_message() {
        let details = PolicyDecisionDetails {
            decision: NetworkPolicyDecision::Ask,
            reason: REASON_NOT_ALLOWED,
            source: NetworkDecisionSource::Decider,
            protocol: NetworkProtocol::HttpsConnect,
            host: "api.example.com",
            port: 443,
        };

        let message = blocked_message_with_policy(REASON_NOT_ALLOWED, &details);
        assert_eq!(message, "Domain not in allowlist.");
    }
}
