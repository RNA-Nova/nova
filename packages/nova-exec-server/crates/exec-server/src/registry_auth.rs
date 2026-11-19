use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;

use http::HeaderMap;
use http::HeaderValue;
use http::header::AUTHORIZATION;
use nova_exec_server_http_client::Request;
use nova_exec_server_http_client::TransportError;

/// Registry 鉴权错误；与底层 HTTP 错误的可重试分类保持隔离。
#[derive(Debug, thiserror::Error)]
pub enum AuthError {
    #[error("request auth build error: {0}")]
    Build(String),
    #[error("transient auth error: {0}")]
    Transient(String),
}

impl From<AuthError> for TransportError {
    fn from(error: AuthError) -> Self {
        match error {
            AuthError::Build(message) => TransportError::Build(message),
            AuthError::Transient(message) => TransportError::Network(message),
        }
    }
}

pub type AuthProviderFuture<'a> =
    Pin<Box<dyn Future<Output = Result<Request, AuthError>> + Send + 'a>>;
pub type AuthHeadersFuture<'a> =
    Pin<Box<dyn Future<Output = Result<HeaderMap, AuthError>> + Send + 'a>>;

/// 为 Registry HTTP 与 Direct WebSocket 请求附加鉴权。
///
/// 静态 Nova bearer token 使用默认实现；AWS 等请求签名提供方可覆盖
/// `apply_auth`，端点逻辑无需感知具体凭证体系。
pub trait AuthProvider: Send + Sync {
    fn add_auth_headers(&self, headers: &mut HeaderMap);

    fn to_auth_headers(&self) -> HeaderMap {
        let mut headers = HeaderMap::new();
        self.add_auth_headers(&mut headers);
        headers
    }

    fn resolve_auth_headers(&self) -> AuthHeadersFuture<'_> {
        Box::pin(async { Ok(self.to_auth_headers()) })
    }

    fn apply_auth(&self, request: Request) -> AuthProviderFuture<'_> {
        Box::pin(async move {
            let mut request = request;
            request.headers.extend(self.resolve_auth_headers().await?);
            Ok(request)
        })
    }
}

pub type SharedAuthProvider = Arc<dyn AuthProvider>;

/// Nova 自有 Registry 的静态 bearer token。
#[derive(Clone)]
pub struct RegistryBearerAuth {
    header: HeaderValue,
}

impl RegistryBearerAuth {
    pub fn new(token: &str) -> Result<Self, AuthError> {
        if token.is_empty() {
            return Err(AuthError::Build(
                "registry bearer token must not be empty".to_string(),
            ));
        }
        let mut header = HeaderValue::from_str(&format!("Bearer {token}"))
            .map_err(|_| AuthError::Build("registry bearer token is invalid".to_string()))?;
        header.set_sensitive(true);
        Ok(Self { header })
    }

    pub fn shared(token: &str) -> Result<SharedAuthProvider, AuthError> {
        Ok(Arc::new(Self::new(token)?))
    }
}

impl AuthProvider for RegistryBearerAuth {
    fn add_auth_headers(&self, headers: &mut HeaderMap) {
        headers.insert(AUTHORIZATION, self.header.clone());
    }
}
