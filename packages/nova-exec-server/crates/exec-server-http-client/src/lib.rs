mod client;
mod client_builder;
mod client_tls;
mod custom_ca;
mod error;
mod network_policy;
mod outbound_proxy;
mod request;
mod request_builder;
mod request_draft;
mod response;
mod retry_after;
mod route_aware_client_pool;
mod route_aware_redirect;
mod tls_backend_fallback;
mod transport;

pub use crate::client::HttpClient;
pub use crate::client::HttpError;
pub use crate::client_builder::HttpClientBuilder;
pub use crate::client_tls::HttpClientTlsConfig;
pub use crate::custom_ca::BuildCustomCaTransportError;
/// Test-only subprocess hook for custom CA coverage.
///
/// This stays public only so the `custom_ca_probe` binary target can reuse the shared helper. It
/// is hidden from normal docs because ordinary callers should use
/// [`build_reqwest_client_with_custom_ca`] instead.
#[doc(hidden)]
pub use crate::custom_ca::build_reqwest_client_for_subprocess_tests;
pub use crate::custom_ca::build_reqwest_client_with_custom_ca;
pub use crate::custom_ca::build_rustls_client_config_with_custom_ca;
pub use crate::custom_ca::maybe_build_rustls_client_config_with_custom_ca;
pub use crate::error::StreamError;
pub use crate::error::TransportError;
// 对位 codex 973ec2942c（#45503）：可撤销应用网络策略原语整套。
pub use crate::network_policy::DestinationPolicy;
pub use crate::network_policy::NetworkPermit;
pub use crate::network_policy::NetworkPolicy;
pub use crate::network_policy::NetworkPolicyController;
pub use crate::network_policy::NetworkPolicyDenied;
pub use crate::network_policy::NetworkPolicyRevision;
pub use crate::outbound_proxy::BuildRouteAwareHttpClientError;
pub use crate::outbound_proxy::ClientRouteClass;
pub use crate::outbound_proxy::HttpClientFactory;
#[cfg(target_os = "macos")]
pub use crate::outbound_proxy::MacosSystemProxyConfiguration;
pub use crate::outbound_proxy::OutboundProxyPolicy;
pub use crate::outbound_proxy::OutboundProxyRoute;
pub use crate::outbound_proxy::RouteFailureClass;
#[doc(hidden)]
pub use crate::outbound_proxy::cache_system_proxy_route_for_test;
#[cfg(target_os = "macos")]
pub use crate::outbound_proxy::macos_system_proxy_configuration;
pub use crate::request::EncodedJsonBody;
pub use crate::request::PreparedRequestBody;
pub use crate::request::Request;
pub use crate::request::RequestBody;
pub use crate::request::RequestCompression;
pub use crate::request::Response;
pub use crate::request_builder::RequestBuilder;
pub use crate::response::HttpResponse;
pub use crate::retry_after::RetryAfter;
pub use crate::route_aware_client_pool::RouteAwareClientPool;
pub use crate::route_aware_client_pool::RouteAwareClientPoolError;
pub use crate::route_aware_client_pool::RouteAwareRequestError;
pub use crate::transport::ByteStream;
pub use crate::transport::HttpTransport;
pub use crate::transport::ReqwestTransport;
pub use crate::transport::StreamResponse;

// 裁点（对位 codex 39f4277087）：锚点的 chatgpt_cloudflare_cookies / chatgpt_hosts /
// windows_tls 模块不镜像——ChatGPT 亲和按 nova 纯度边界移除，windows_tls 属
// Windows 专用面（本批不引入；macOS/Linux 路径无消费方）。
