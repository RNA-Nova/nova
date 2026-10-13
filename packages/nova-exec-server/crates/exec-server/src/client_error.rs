//! Classifies typed executor failures without treating error text as a retry signal.
//!
//! 对位 codex 39f4277087 `exec-server/src/client_error.rs` 整文件。唯一差异：
//! environment registry 族错误（`EnvironmentRegistryHttp`/`EnvironmentRegistryRequest`
//! 等）在 nova 已随 deferred-environment 特性从 `ExecServerError` 裁剪（对位
//! `client_recovery.rs` 移植 `is_retryable_recovery_error` 时的既有裁剪），故锚点
//! 中的 `is_retryable_registry_error(error)` 臂在此不存在；其余分类逐行对位。

use std::io::ErrorKind;

use http::StatusCode;
use tokio_tungstenite::tungstenite::Error as WebSocketError;
use tokio_tungstenite::tungstenite::error::ProtocolError;

use crate::ExecServerError;
use crate::rpc::SESSION_ALREADY_ATTACHED_ERROR_CODE;

impl ExecServerError {
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
