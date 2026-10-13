//! 发出进程生命周期遥测事件，并约束 tracing 记录的进程标识。
//! 生命周期事件保留启动时捕获的身份，跨重连与后续操作不刷新。
//! （对位 codex 39f4277087 `process_telemetry.rs` 整文件移植——事件名按
//! `codex.*`→`nova.*`、target 按 `codex_otel`→`nova_exec_server_otel` 改名）

use std::sync::Arc;

use nova_exec_server_sandboxing::SandboxType;
use opentelemetry::trace::SpanContext;

use crate::telemetry::ExecutorRegistration;

/// 启动时捕获的日志字段，从不从恢复的会话刷新。
#[derive(Clone, Default)]
pub(crate) struct ProcessTelemetry {
    pub(crate) launch_context: Option<SpanContext>,
    pub(crate) thread_id: Option<String>,
    pub(crate) tool_call_id: Option<String>,
    pub(crate) executor_registration: Option<Arc<ExecutorRegistration>>,
}

/// 生命周期事件；仅进程退出后携带结果字段。
pub(crate) enum ProcessTelemetryEvent {
    Start,
    SpawnFailed,
    SandboxDenied,
    Exit {
        exit_code: i32,
        termination_requested: bool,
    },
}

impl ProcessTelemetry {
    pub(crate) fn log(&self, event: ProcessTelemetryEvent, sandbox: SandboxType) {
        let (event_name, exit_code, termination_requested, reason) = match event {
            ProcessTelemetryEvent::Start => ("nova.exec_server.process_start", None, None, None),
            ProcessTelemetryEvent::SpawnFailed => {
                ("nova.exec_server.process_spawn_failed", None, None, None)
            }
            ProcessTelemetryEvent::SandboxDenied => (
                "nova.exec_server.sandbox_denied",
                None,
                None,
                Some("inferred_denial"),
            ),
            ProcessTelemetryEvent::Exit {
                exit_code,
                termination_requested,
            } => (
                "nova.exec_server.process_exit",
                Some(exit_code),
                Some(termination_requested),
                None,
            ),
        };
        let trace_id = self
            .launch_context
            .as_ref()
            .map(|span| span.trace_id().to_string());
        let span_id = self
            .launch_context
            .as_ref()
            .map(|span| span.span_id().to_string());
        tracing::event!(
            target: "nova_exec_server_otel.log_only",
            tracing::Level::INFO,
            event.name = event_name,
            launch.trace_id = trace_id.as_deref(),
            launch.span_id = span_id.as_deref(),
            conversation.id = self.thread_id.as_deref(),
            tool.call_id = self.tool_call_id.as_deref(),
            executor.environment_id = self.executor_registration.as_ref().map(|registration| registration.environment_id.as_str()),
            executor.registration_id = self.executor_registration.as_ref().map(|registration| registration.executor_registration_id.as_str()),
            sandbox.type = ?sandbox,
            process.exit_code = exit_code,
            process.termination_requested = termination_requested,
            reason,
        );
    }
}

pub(crate) fn trace_process_id(process_id: &str) -> Option<&str> {
    (!process_id.is_empty() && process_id.len() <= 64).then_some(process_id)
}
