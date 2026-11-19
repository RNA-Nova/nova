//! Classifies typed executor failures without treating error text as a retry signal.
//!
//! 对位 codex 39f4277087 `exec-server/src/client_error.rs`；Registry 错误族
//! 随 Noise Relay 恢复，传输与鉴权失败按各自类型分类。

use std::io::ErrorKind;

use http::StatusCode;
use tokio_tungstenite::tungstenite::Error as WebSocketError;
use tokio_tungstenite::tungstenite::error::ProtocolError;

use crate::ExecServerError;
use crate::rpc::SESSION_ALREADY_ATTACHED_ERROR_CODE;
use nova_exec_server_http_client::NetworkPolicyDenied;
use nova_exec_server_http_client::RouteAwareRequestError;

impl ExecServerError {
    pub(crate) fn application_network_policy_denial(&self) -> Option<NetworkPolicyDenied> {
        match self {
            Self::ConnectionAttempt(source) => source.application_network_policy_denial(),
            Self::EnvironmentRegistryRequest(RouteAwareRequestError::Policy(denial)) => {
                Some(*denial)
            }
            _ => None,
        }
    }

    /// Identifies transient failures that read-only preparation can retry.
    ///
    /// Includes transient handshake responses and connection EOFs, including wrapped
    /// failures. Authentication, TLS, and malformed protocol errors are excluded.
    /// （对位 codex 同名 `pub` 方法：crate 外调用方也用它分类只读准备阶段错误。）
    pub fn is_retryable_preparation_error(&self) -> bool {
        let mut error = self;
        while let Self::ConnectionAttempt(source) = error {
            error = source;
        }
        match error {
            Self::Closed
            | Self::Disconnected(_)
            | Self::WebSocketConnectTimeout { .. }
            | Self::InitializeTimedOut { .. } => true,
            Self::WebSocketConnect { source, .. } => match source {
                WebSocketError::ConnectionClosed
                | WebSocketError::AlreadyClosed
                | WebSocketError::Protocol(
                    ProtocolError::HandshakeIncomplete
                    | ProtocolError::ResetWithoutClosingHandshake,
                ) => true,
                WebSocketError::Http(response) => {
                    response.status().is_server_error()
                        || matches!(
                            response.status(),
                            StatusCode::REQUEST_TIMEOUT
                                | StatusCode::CONFLICT
                                | StatusCode::TOO_MANY_REQUESTS
                        )
                }
                WebSocketError::Io(error) => matches!(
                    error.kind(),
                    ErrorKind::ConnectionRefused
                        | ErrorKind::ConnectionReset
                        | ErrorKind::ConnectionAborted
                        | ErrorKind::NetworkDown
                        | ErrorKind::NetworkUnreachable
                        | ErrorKind::HostUnreachable
                        | ErrorKind::BrokenPipe
                        | ErrorKind::NotConnected
                        | ErrorKind::UnexpectedEof
                        | ErrorKind::TimedOut
                ),
                _ => false,
            },
            Self::Server { code, .. } if *code == SESSION_ALREADY_ATTACHED_ERROR_CODE => true,
            _ => false,
        }
    }
}
