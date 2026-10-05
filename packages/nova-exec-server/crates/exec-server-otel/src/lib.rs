pub(crate) mod config;
pub(crate) mod metrics;
pub(crate) mod provider;
pub(crate) mod trace_context;

mod otlp;
mod targets;

pub use crate::config::OtelExporter;
pub use crate::config::OtelHttpProtocol;
pub use crate::config::OtelSettings;
pub use crate::config::OtelTlsConfig;
pub use crate::config::StatsigMetricsSettings;
pub use crate::config::load_otel_settings;
pub use crate::config::validate_span_attributes;
pub use crate::metrics::*;
pub use crate::provider::OtelProvider;
pub use crate::trace_context::context_from_w3c_trace_context;
pub use crate::trace_context::current_span_trace_id;
pub use crate::trace_context::current_span_w3c_trace_context;
pub use crate::trace_context::inject_span_w3c_trace_headers;
pub use crate::trace_context::set_parent_from_context;
pub use crate::trace_context::set_parent_from_w3c_trace_context;
pub use crate::trace_context::span_w3c_trace_context;
pub use crate::trace_context::validate_tracestate_entries;
pub use crate::trace_context::validate_tracestate_member;
pub use nova_exec_server_utils_string::sanitize_metric_tag_value;

/// Install externally managed process-global metrics.
///
/// Call this once during single-threaded startup, before any instruments are
/// registered. Keep the returned handle to flush and shut down the exporter
/// owned by this installation.
pub fn install_global_metrics(metrics: MetricsClient) -> MetricsClient {
    crate::metrics::install_global(metrics)
}

/// Returns the resolved Statsig metrics settings for the globally installed
/// metrics pipeline, when the parent process provided them.
///
/// 对位 codex `global_statsig_metrics_settings`（lib.rs）。Windows 沙箱 setup
/// helper 用它把 settings 传进 elevation payload。
pub fn global_statsig_metrics_settings() -> Option<StatsigMetricsSettings> {
    crate::metrics::global_statsig_settings()
}
