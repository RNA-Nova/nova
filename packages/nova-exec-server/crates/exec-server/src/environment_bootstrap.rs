use nova_exec_server_http_client::HttpClientFactory;

use crate::EnvironmentManager;
use crate::ExecServerError;
use crate::ExecServerRuntimePaths;
use crate::environment_provider::EnvironmentDefault;
use crate::environment_provider::EnvironmentProviderSnapshot;

#[derive(Debug)]
pub(crate) enum PreparedEnvironmentSource {
    Snapshot(EnvironmentProviderSnapshot),
}

/// Holds discovered execution environments before their HTTP policy is resolved.
///
/// Preparing environments does not start remote connections. Callers can inspect
/// the default environment to choose config-loading behavior and then build the
/// manager with the effective outbound HTTP policy.
///
/// （对位 codex `environment_bootstrap.rs`——Noise 变体随中继挂账剔除，静态
/// 端点只有 Snapshot 一路。）
#[derive(Debug)]
pub struct PreparedEnvironmentManager {
    pub(crate) source: PreparedEnvironmentSource,
}

impl PreparedEnvironmentManager {
    /// Returns whether the discovered default environment is remote.
    pub fn default_environment_is_remote(&self) -> bool {
        match &self.source {
            PreparedEnvironmentSource::Snapshot(snapshot) => match &snapshot.default {
                EnvironmentDefault::Disabled => false,
                EnvironmentDefault::EnvironmentId(default_id) => snapshot
                    .environments
                    .iter()
                    .any(|(environment_id, _)| environment_id == default_id),
            },
        }
    }

    /// Builds the manager and starts remote connections using the supplied policy.
    pub fn build(
        self,
        local_runtime_paths: Option<ExecServerRuntimePaths>,
        http_client_factory: HttpClientFactory,
    ) -> Result<EnvironmentManager, ExecServerError> {
        match self.source {
            PreparedEnvironmentSource::Snapshot(snapshot) => EnvironmentManager::from_snapshot(
                snapshot,
                local_runtime_paths,
                http_client_factory,
            ),
        }
    }
}

#[cfg(test)]
#[path = "environment_bootstrap_tests.rs"]
mod tests;
