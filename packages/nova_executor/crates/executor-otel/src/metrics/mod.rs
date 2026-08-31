mod client;
mod config;
mod error;
pub(crate) mod validation;

pub use crate::metrics::client::MetricsClient;
pub use crate::metrics::config::MetricsConfig;
pub use crate::metrics::config::MetricsExporter;
pub use crate::metrics::error::MetricsError;
pub use crate::metrics::error::Result;
use std::sync::Arc;
use std::sync::OnceLock;
use std::sync::RwLock;

static GLOBAL_METRICS: OnceLock<MetricsClient> = OnceLock::new();

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
    metrics
}

pub fn global() -> Option<MetricsClient> {
    GLOBAL_METRICS.get().cloned()
}
