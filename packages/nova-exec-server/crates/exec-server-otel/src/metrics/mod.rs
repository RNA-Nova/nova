pub(crate) mod buffered;
mod client;
mod config;
mod error;
pub(crate) mod names;
mod process;
pub(crate) mod runtime_metrics;
pub(crate) mod tags;
mod timer;
pub(crate) mod validation;

pub use crate::metrics::buffered::record_global_operation;
pub use crate::metrics::client::MetricsClient;
pub use crate::metrics::config::MetricsConfig;
pub use crate::metrics::config::MetricsExporter;
pub use crate::metrics::error::MetricsError;
pub use crate::metrics::error::Result;
pub use crate::metrics::process::record_process_start_once;
pub use crate::metrics::timer::Timer;
pub use names::*;
pub use tags::ORIGINATOR_TAG;
pub use tags::SessionMetricTagValues;
pub use tags::bounded_originator_tag_value;
use crate::config::StatsigMetricsSettings;
use std::sync::Arc;
use std::sync::OnceLock;
use std::sync::RwLock;

static GLOBAL_METRICS: OnceLock<MetricsClient> = OnceLock::new();
static GLOBAL_STATSIG_METRICS_SETTINGS: OnceLock<StatsigMetricsSettings> = OnceLock::new();

pub(crate) fn install_global(mut metrics: MetricsClient) -> MetricsClient {
    let active = GLOBAL_METRICS
        .get()
        .and_then(|current| current.active.clone())
        .unwrap_or_else(|| Arc::new(RwLock::new(Arc::clone(&metrics.inner))));
    *active
        .write()
        .unwrap_or_else(std::sync::PoisonError::into_inner) = Arc::clone(&metrics.inner);
    metrics.active = Some(active);
    let _ = GLOBAL_METRICS.set(metrics.clone());
    buffered::GLOBAL.enable(&metrics);
    metrics
}

pub fn global() -> Option<MetricsClient> {
    GLOBAL_METRICS.get().cloned()
}

#[allow(dead_code)] // 对位 codex `install_global_statsig_settings`；nova 侧暂无安装方
pub(crate) fn install_global_statsig_settings(settings: StatsigMetricsSettings) {
    let _ = GLOBAL_STATSIG_METRICS_SETTINGS.set(settings);
}

/// 对位 codex `global_statsig_settings`。codex 另有一层
/// `metrics.active_inner().network_policy.is_managed()` 门控（托管网络下扣留
/// settings）——nova 的 MetricsClientInner 无 network_policy 字段，该门控未移植。
pub(crate) fn global_statsig_settings() -> Option<StatsigMetricsSettings> {
    GLOBAL_STATSIG_METRICS_SETTINGS.get().cloned()
}
