//! 出站请求的认证抽象（registry / relay 等 executor 主动发起的 HTTP 调用用）。
//!
//! 只保留 header 形态：`add_auth_headers` 为唯一必须实现的方法；
//! `to_auth_headers` 为便捷糖。原 `apply_auth`（请求签名形态）随模型 API
//! 层一并移除——executor 作为通用执行后端不持有需要签名的出站调用。

use http::HeaderMap;
use std::sync::Arc;

/// 出站请求认证提供者。
pub trait AuthProvider: Send + Sync {
    /// 向请求头注入认证信息。实现应廉价且无阻塞。
    fn add_auth_headers(&self, headers: &mut HeaderMap);

    /// 以新 HeaderMap 形式返回全部认证头（便捷糖）。
    fn to_auth_headers(&self) -> HeaderMap {
        let mut headers = HeaderMap::new();
        self.add_auth_headers(&mut headers);
        headers
    }
}

/// 共享认证句柄。
pub type SharedAuthProvider = Arc<dyn AuthProvider>;

/// 无认证提供者（本地模式 / SSH 隧道模式）。
#[derive(Clone, Debug, Default)]
pub struct NoopAuthProvider;

impl AuthProvider for NoopAuthProvider {
    fn add_auth_headers(&self, _headers: &mut HeaderMap) {}
}

/// Bearer Token 认证提供者。
#[derive(Clone, Debug)]
pub struct BearerTokenAuthProvider {
    token: String,
}

impl BearerTokenAuthProvider {
    pub fn new(token: String) -> Self {
        Self { token }
    }
}

impl AuthProvider for BearerTokenAuthProvider {
    fn add_auth_headers(&self, headers: &mut HeaderMap) {
        headers.insert(
            http::header::AUTHORIZATION,
            format!("Bearer {}", self.token).parse().unwrap(),
        );
    }
}
