use crate::exec_output::ExecToolCallOutput;
use crate::network_policy::NetworkPolicyDecisionPayload;
use std::fmt;
use std::io;
use std::time::Duration;
use strum_macros::EnumDiscriminants;
use thiserror::Error;

/// 沙箱执行链路的错误类型。
#[derive(Error, Debug)]
pub enum SandboxErr {
    /// Error from sandbox execution
    #[error(
        "sandbox denied exec error, exit code: {}, stdout: {}, stderr: {}",
        .output.exit_code, .output.stdout.text, .output.stderr.text
    )]
    Denied {
        output: Box<ExecToolCallOutput>,
        network_policy_decision: Option<NetworkPolicyDecisionPayload>,
    },

    /// Error from linux seccomp filter setup
    #[cfg(target_os = "linux")]
    #[error("seccomp setup error")]
    SeccompInstall(#[from] seccompiler::Error),

    /// Error from linux seccomp backend
    #[cfg(target_os = "linux")]
    #[error("seccomp backend error")]
    SeccompBackend(#[from] seccompiler::BackendError),

    /// Command timed out
    #[error("command timed out")]
    Timeout { output: Box<ExecToolCallOutput> },

    /// Command was killed by a signal
    #[error("command was killed by a signal")]
    Signal(i32),

    /// Error from linux landlock
    #[error("Landlock was not able to fully enforce all sandbox rules")]
    LandlockRestrict,
}

pub struct CodexErr {
    details: CodexErrorDetails,
    retry_delay: Option<Duration>,
}

#[derive(Error, Debug, EnumDiscriminants)]
#[strum_discriminants(name(CodexErrKind))]
#[strum_discriminants(derive(serde::Serialize))]
#[strum_discriminants(serde(rename_all = "snake_case"))]
#[strum_discriminants(doc = "The payload-free semantic category used for analytics.")]
pub enum CodexErrorDetails {
    /// Invalid request.
    #[error("{0}")]
    InvalidRequest(String),
    /// Sandbox error
    #[error("sandbox error: {0}")]
    Sandbox(#[from] SandboxErr),
    #[error("codex-linux-sandbox was required but not provided")]
    LandlockSandboxExecutableNotProvided,
    #[error("unsupported operation: {0}")]
    UnsupportedOperation(String),
    #[error("Fatal error: {0}")]
    Fatal(String),
    #[error(transparent)]
    Io(#[from] io::Error),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}


// 兼容宏：让调用方以 CodexErr::Variant 形态构造错误（同 codex 上游）。
macro_rules! codex_err_unit_constructors {
    ($($variant:ident),* $(,)?) => {
        $(
            #[doc(hidden)]
            #[allow(non_upper_case_globals)]
            pub const $variant: Self = Self {
                details: CodexErrorDetails::$variant,
                retry_delay: None,
            };
        )*
    };
}

macro_rules! codex_err_tuple_constructors {
    ($($(#[$attr:meta])* $variant:ident($value:ident: $value_type:ty)),* $(,)?) => {
        $(
            $(#[$attr])*
            #[doc(hidden)]
            #[allow(non_snake_case)]
            pub fn $variant($value: $value_type) -> Self {
                CodexErrorDetails::$variant($value).into()
            }
        )*
    };
}

impl CodexErr {
    /// 底层错误类别（供外部按类别分流）。
    pub fn details(&self) -> &CodexErrorDetails {
        &self.details
    }

    codex_err_unit_constructors!(
        LandlockSandboxExecutableNotProvided,
    );

    codex_err_tuple_constructors!(
        InvalidRequest(message: String),
        UnsupportedOperation(message: String),
        Fatal(message: String),
    );
}

impl From<&CodexErr> for CodexErrKind {
    fn from(error: &CodexErr) -> Self {
        error.details().into()
    }
}

impl fmt::Debug for CodexErr {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        fmt::Debug::fmt(&self.details, formatter)
    }
}

impl fmt::Display for CodexErr {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        fmt::Display::fmt(&self.details, formatter)
    }
}

impl std::error::Error for CodexErr {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        self.details.source()
    }
}

impl From<CodexErrorDetails> for CodexErr {
    fn from(details: CodexErrorDetails) -> Self {
        Self { details, retry_delay: None }
    }
}

impl From<SandboxErr> for CodexErr {
    fn from(error: SandboxErr) -> Self {
        CodexErrorDetails::from(error).into()
    }
}

impl From<io::Error> for CodexErr {
    fn from(error: io::Error) -> Self {
        CodexErrorDetails::from(error).into()
    }
}

impl From<serde_json::Error> for CodexErr {
    fn from(error: serde_json::Error) -> Self {
        CodexErrorDetails::from(error).into()
    }
}
