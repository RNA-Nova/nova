//! Route-aware local HTTP capability implementation.
//!
//! This code runs wherever the real network request should originate:
//! - in a local environment, that means the orchestrator process
//! - in a remote environment, that means the remote runtime after the
//!   orchestrator has forwarded `http/request` over JSON-RPC

use std::time::Duration;

use futures::FutureExt;
use futures::StreamExt;
use futures::future::BoxFuture;
use http::HeaderMap;
use http::HeaderName;
use http::HeaderValue;
use http::Method;
use nova_executor_http_client::ClientRouteClass;
use nova_executor_http_client::HttpClientFactory;
use nova_executor_http_client::RouteAwareClientPool;
use nova_executor_http_client::RouteAwareRequestError;
use nova_executor_protocol::JSONRPCErrorError;
use tracing::Instrument;
use url::Url;

use super::HttpResponseBodyStream;
use super::response_body_stream::send_body_delta;
use crate::HttpClient;
use crate::client::ExecServerError;
use crate::protocol::HttpHeader;
use crate::protocol::HttpRedirectPolicy;
use crate::protocol::HttpRequestBodyDeltaNotification;
use crate::protocol::HttpRequestParams;
use crate::protocol::HttpRequestResponse;
use crate::protocol::MAX_HTTP_BODY_DELTA_BYTES;
use crate::rpc::RpcNotificationSender;
use crate::rpc::internal_error;
use crate::rpc::invalid_params;
use nova_executor_network_proxy::PROXY_ATTRIBUTION_TOKEN_ENV_KEY;

/// http/request 的 valueEnvVar 保护名单（对位 codex HTTP_HEADER_ENV_DENYLIST）：
/// 执行端本机的敏感环境变量不许经客户端点名的头代发——executor 侧凭据
/// 不可跨线泄露给客户端。收 nova 自家 token + 通用云凭据 + 本仓供应商 key。
const HTTP_HEADER_ENV_DENYLIST: &[&str] = &[
    PROXY_ATTRIBUTION_TOKEN_ENV_KEY,
    "VOLCENGINE_API_KEY",
    "MOONSHOT_API_KEY",
    "KIMI_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AZURE_CLIENT_SECRET",
    "AZURE_FEDERATED_TOKEN_FILE",
    "GOOGLE_APPLICATION_CREDENTIALS",
];

/// HTTP capability implementation backed by the shared route-aware transport.
#[derive(Clone)]
pub struct RouteAwareHttpClient {
    follow_redirects: RouteAwareClientPool,
    stop_redirects: RouteAwareClientPool,
}

/// Streaming response state held between the initial HTTP response and
/// downstream body-delta forwarding.
pub(crate) struct PendingRouteAwareHttpBodyStream {
    pub(crate) request_id: String,
    pub(crate) response: nova_executor_http_client::HttpResponse,
}

/// Validates `http/request` parameters and runs the actual HTTP call used
/// by the exec-server route and the local [`HttpClient`] backend.
pub(crate) struct RouteAwareHttpRequestRunner {
    client: RouteAwareClientPool,
}

impl RouteAwareHttpClient {
    pub fn new(http_client_factory: HttpClientFactory) -> Self {
        Self {
            follow_redirects: RouteAwareClientPool::new_without_request_logging(
                http_client_factory.clone(),
                // Delegated HTTP targets arbitrary endpoints; route class only labels diagnostics.
                ClientRouteClass::Other,
            ),
            stop_redirects: RouteAwareClientPool::new_without_redirects_or_request_logging(
                http_client_factory,
                // Proxy routing comes from the factory, not this diagnostic-only route class.
                ClientRouteClass::Other,
            ),
        }
    }

    pub(crate) fn runner(
        &self,
        redirect_policy: HttpRedirectPolicy,
    ) -> RouteAwareHttpRequestRunner {
        let client = match redirect_policy {
            HttpRedirectPolicy::Follow => self.follow_redirects.clone(),
            HttpRedirectPolicy::Stop => self.stop_redirects.clone(),
        };
        RouteAwareHttpRequestRunner { client }
    }
}

impl HttpClient for RouteAwareHttpClient {
    fn http_request(
        &self,
        params: HttpRequestParams,
    ) -> BoxFuture<'_, Result<HttpRequestResponse, ExecServerError>> {
        async move {
            let runner = self.runner(params.redirect_policy);
            let (response, _) = runner
                .run(HttpRequestParams {
                    stream_response: false,
                    ..params
                })
                .await
                .map_err(|error| ExecServerError::HttpRequest(error.message))?;
            Ok(response)
        }
        .boxed()
    }

    fn http_request_stream(
        &self,
        params: HttpRequestParams,
    ) -> BoxFuture<'_, Result<(HttpRequestResponse, HttpResponseBodyStream), ExecServerError>> {
        async move {
            let runner = self.runner(params.redirect_policy);
            let (response, pending_stream) = runner
                .run(HttpRequestParams {
                    stream_response: true,
                    ..params
                })
                .await
                .map_err(|error| ExecServerError::HttpRequest(error.message))?;
            let pending_stream = pending_stream.ok_or_else(|| {
                ExecServerError::Protocol(
                    "http request stream did not return a response body stream".to_string(),
                )
            })?;
            Ok((
                response,
                HttpResponseBodyStream::local(pending_stream.response),
            ))
        }
        .boxed()
    }
}

impl RouteAwareHttpRequestRunner {
    pub(crate) async fn run(
        &self,
        params: HttpRequestParams,
    ) -> Result<(HttpRequestResponse, Option<PendingRouteAwareHttpBodyStream>), JSONRPCErrorError>
    {
        let method = Method::from_bytes(params.method.as_bytes())
            .map_err(|error| invalid_params(format!("http/request method is invalid: {error}")))?;
        let url = Url::parse(&params.url)
            .map_err(|error| invalid_params(format!("http/request url is invalid: {error}")))?;
        match url.scheme() {
            "http" | "https" => {}
            scheme => {
                return Err(invalid_params(format!(
                    "http/request only supports http and https URLs, got {scheme}"
                )));
            }
        }

        let request_span = tracing::info_span!(
            "nova.executor.http_request",
            otel.kind = "client",
            http.request.method = method.as_str(),
            server.address = url.host_str().unwrap_or_default(),
            server.port = u64::from(url.port_or_known_default().unwrap_or_default()),
            http.response.status_code = tracing::field::Empty,
            error.type = tracing::field::Empty,
        );
        let mut headers = Self::build_headers(params.headers)?;
        nova_executor_otel::inject_span_w3c_trace_headers(&request_span, &mut headers);
        let mut request = self.client.request(method.clone(), url).headers(headers);
        if let Some(body) = params.body {
            request = request.body(body.into_inner());
        }
        if let Some(timeout_ms) = params.timeout_ms {
            request = request.timeout(Duration::from_millis(timeout_ms));
        }

        let response = match request.send().instrument(request_span.clone()).await {
            Ok(response) => response,
            Err(error) => {
                request_span.record("error.type", "request");
                let error_message = error.to_string();
                log_send_error(&method, error);
                return Err(internal_error(format!(
                    "http/request failed: {error_message}"
                )));
            }
        };
        let status = response.status().as_u16();
        request_span.record("http.response.status_code", u64::from(status));
        let headers = Self::response_headers(response.headers());

        if params.stream_response {
            return Ok((
                HttpRequestResponse {
                    status,
                    headers,
                    body: Vec::new().into(),
                },
                Some(PendingRouteAwareHttpBodyStream {
                    request_id: params.request_id,
                    response,
                }),
            ));
        }

        let body = response.bytes().await.map_err(|error| {
            internal_error(format!(
                "failed to read http/request response body: {error}"
            ))
        })?;

        Ok((
            HttpRequestResponse {
                status,
                headers,
                body: body.to_vec().into(),
            },
            None,
        ))
    }

    pub(crate) async fn stream_body(
        pending_stream: PendingRouteAwareHttpBodyStream,
        notifications: RpcNotificationSender,
    ) {
        let PendingRouteAwareHttpBodyStream {
            request_id,
            response,
        } = pending_stream;
        let mut seq = 1;
        let mut body = response.bytes_stream();
        while let Some(chunk) = body.next().await {
            match chunk {
                Ok(bytes) => {
                    for chunk in bytes.chunks(MAX_HTTP_BODY_DELTA_BYTES) {
                        if !send_body_delta(
                            &notifications,
                            HttpRequestBodyDeltaNotification {
                                request_id: request_id.clone(),
                                seq,
                                delta: chunk.to_vec().into(),
                                done: false,
                                error: None,
                            },
                        )
                        .await
                        {
                            return;
                        }
                        seq += 1;
                    }
                }
                Err(error) => {
                    let _ = send_body_delta(
                        &notifications,
                        HttpRequestBodyDeltaNotification {
                            request_id,
                            seq,
                            delta: Vec::new().into(),
                            done: true,
                            error: Some(error.to_string()),
                        },
                    )
                    .await;
                    return;
                }
            }
        }

        let _ = send_body_delta(
            &notifications,
            HttpRequestBodyDeltaNotification {
                request_id,
                seq,
                delta: Vec::new().into(),
                done: true,
                error: None,
            },
        )
        .await;
    }

    fn build_headers(headers: Vec<HttpHeader>) -> Result<HeaderMap, JSONRPCErrorError> {
        let mut header_map = HeaderMap::new();
        for header in headers {
            let name = HeaderName::from_bytes(header.name.as_bytes()).map_err(|error| {
                invalid_params(format!("http/request header name is invalid: {error}"))
            })?;
            let value = match header.value_env_var {
                Some(env_var) => {
                    // 执行端敏感变量拒代发（对位 codex build_headers）
                    if HTTP_HEADER_ENV_DENYLIST
                        .iter()
                        .any(|denied| denied.eq_ignore_ascii_case(&env_var))
                    {
                        return Err(invalid_params(format!(
                            "http/request header {} cannot use executor environment variable {env_var}",
                            header.name
                        )));
                    }
                    let env_value = std::env::var(&env_var).map_err(|_| {
                        invalid_params(format!(
                            "http/request header {} requires executor environment variable {env_var}",
                            header.name
                        ))
                    })?;
                    if env_value.is_empty() {
                        return Err(invalid_params(format!(
                            "http/request header {} requires a non-empty executor environment variable {env_var}",
                            header.name
                        )));
                    }
                    format!("{}{env_value}", header.value)
                }
                None => header.value,
            };
            let value = HeaderValue::from_str(&value).map_err(|error| {
                invalid_params(format!(
                    "http/request header value is invalid for {}: {error}",
                    header.name
                ))
            })?;
            header_map.append(name, value);
        }
        Ok(header_map)
    }

    fn response_headers(headers: &HeaderMap) -> Vec<HttpHeader> {
        headers
            .iter()
            .filter_map(|(name, value)| {
                Some(HttpHeader {
                    name: name.as_str().to_string(),
                    value: value.to_str().ok()?.to_string(),
                    value_env_var: None,
                })
            })
            .collect()
    }
}

fn log_send_error(method: &Method, error: RouteAwareRequestError) {
    let error_is_timeout = error.is_timeout();
    let error_is_connect = error.is_connect();
    let error = match error {
        RouteAwareRequestError::Request(error) => error.without_url().to_string(),
        error => error.to_string(),
    };
    tracing::warn!(
        http_method = method.as_str(),
        error_is_timeout,
        error_is_connect,
        error = %error,
        "http/request send failed"
    );
}
