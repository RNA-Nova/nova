use crate::exec_output::ExecToolCallOutput;
use crate::network_policy::NetworkPolicyDecisionPayload;
use std::fmt;
use std::io;
use std::time::Duration;
use strum_macros::EnumDiscriminants;
use thiserror::Error;

/// 执行链路默认错误别名。
pub type Result<T, E = ExecErr> = std::result::Result<T, E>;

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

pub struct ExecErr {
    details: ExecErrorDetails,
    retry_delay: Option<Duration>,
}

#[derive(Error, Debug, EnumDiscriminants)]
#[strum_discriminants(name(ExecErrKind))]
#[strum_discriminants(derive(serde::Serialize))]
#[strum_discriminants(serde(rename_all = "snake_case"))]
#[strum_discriminants(doc = "The payload-free semantic category used for analytics.")]
pub enum ExecErrorDetails {
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
    #[cfg(target_os = "linux")]
    #[error(transparent)]
    LandlockRuleset(#[from] landlock::RulesetError),
    #[cfg(target_os = "linux")]
    #[error(transparent)]
    LandlockPathFd(#[from] landlock::PathFdError),
}


// 兼容宏：让调用方以 ExecErr::Variant 形态构造错误（同 codex 上游）。
macro_rules! exec_err_unit_constructors {
    ($($variant:ident),* $(,)?) => {
        $(
            #[doc(hidden)]
            #[allow(non_upper_case_globals)]
            pub const $variant: Self = Self {
                details: ExecErrorDetails::$variant,
                retry_delay: None,
            };
        )*
    };
}

macro_rules! exec_err_tuple_constructors {
    ($($(#[$attr:meta])* $variant:ident($value:ident: $value_type:ty)),* $(,)?) => {
        $(
            $(#[$attr])*
            #[doc(hidden)]
            #[allow(non_snake_case)]
            pub fn $variant($value: $value_type) -> Self {
                ExecErrorDetails::$variant($value).into()
            }
        )*
    };
}

impl ExecErr {
    /// 底层错误类别（供外部按类别分流）。
    pub fn details(&self) -> &ExecErrorDetails {
        &self.details
    }

    exec_err_unit_constructors!(
        LandlockSandboxExecutableNotProvided,
    );

    exec_err_tuple_constructors!(
        Sandbox(error: SandboxErr),
        InvalidRequest(message: String),
        UnsupportedOperation(message: String),
        Fatal(message: String),
    );
}

impl From<&ExecErr> for ExecErrKind {
    fn from(error: &ExecErr) -> Self {
        error.details().into()
    }
}

impl fmt::Debug for ExecErr {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        fmt::Debug::fmt(&self.details, formatter)
    }
}

impl fmt::Display for ExecErr {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        fmt::Display::fmt(&self.details, formatter)
    }
}

impl std::error::Error for ExecErr {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        self.details.source()
    }
}

impl From<ExecErrorDetails> for ExecErr {
    fn from(details: ExecErrorDetails) -> Self {
        Self { details, retry_delay: None }
    }
}

impl From<SandboxErr> for ExecErr {
    fn from(error: SandboxErr) -> Self {
        ExecErrorDetails::from(error).into()
    }
}

impl From<io::Error> for ExecErr {
    fn from(error: io::Error) -> Self {
        ExecErrorDetails::from(error).into()
    }
}

impl From<serde_json::Error> for ExecErr {
    fn from(error: serde_json::Error) -> Self {
        ExecErrorDetails::from(error).into()
    }
}

#[cfg(target_os = "linux")]
impl From<landlock::RulesetError> for ExecErr {
    fn from(error: landlock::RulesetError) -> Self {
        ExecErrorDetails::from(error).into()
    }
}

#[cfg(target_os = "linux")]
impl From<landlock::PathFdError> for ExecErr {
    fn from(error: landlock::PathFdError) -> Self {
        ExecErrorDetails::from(error).into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 兼容构造器生成的变体可经 Display 稳定呈现。
    #[test]
    fn compat_constructors_render_display() {
        let err = ExecErr::Fatal("boom".to_string());
        assert!(err.to_string().contains("Fatal error: boom"));
        assert!(matches!(err.details(), ExecErrorDetails::Fatal(_)));
    }

    /// SandboxErr 经 From 自动归入 Sandbox 类别。
    #[test]
    fn sandbox_err_wraps_into_sandbox_category() {
        let err = ExecErr::from(SandboxErr::LandlockRestrict);
        assert!(matches!(err.details(), ExecErrorDetails::Sandbox(_)));
        assert!(err.to_string().contains("Landlock"));
    }

    /// InvalidRequest 保留原始消息文本。
    #[test]
    fn invalid_request_keeps_message() {
        let err = ExecErr::InvalidRequest("bad args".to_string());
        assert_eq!(err.to_string(), "bad args");
    }
}
