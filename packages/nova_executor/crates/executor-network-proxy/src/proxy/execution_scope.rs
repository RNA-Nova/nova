//! 单次执行的归因作用域（移植自 codex network-proxy `proxy/execution_scope.rs`）。

use super::*;

pub(super) struct ExecutionScope {
    pub(super) environment_id: String,
    pub(super) execution_id: String,
    pub(super) attribution_token: String,
    pub(super) environment_policy: Option<crate::EnvironmentNetworkPolicy>,
    // 丢弃 execution scope 会关闭该 channel，从而取消进行中的远程审批。
    pub(super) lifetime_tx: tokio::sync::watch::Sender<()>,
    state: Arc<NetworkProxyState>,
}

impl Drop for ExecutionScope {
    fn drop(&mut self) {
        self.state.unregister_execution(&self.attribution_token);
    }
}

impl NetworkProxy {
    /// 返回带单次执行归因与 attachment 策略的代理。
    pub fn for_execution(
        &self,
        environment_id: &str,
        execution_id: &str,
        attribution_token: String,
        environment_policy: Option<crate::EnvironmentNetworkPolicy>,
        fallback_policy_decider: Option<Arc<dyn NetworkPolicyDecider>>,
    ) -> Result<Self> {
        anyhow::ensure!(
            self.execution_scope.is_none(),
            "cannot scope an execution-scoped network proxy"
        );
        self.state
            .register_execution(&attribution_token, environment_id, execution_id);

        let (lifetime_tx, _) = tokio::sync::watch::channel(());
        let mut proxy = self.clone();
        proxy.policy_decider = proxy.policy_decider.or(fallback_policy_decider);
        // 严格的 attachment allowlist 不能通过审批回调扩张。
        if matches!(&environment_policy, Some(policy) if policy.managed_allowed_domains_only) {
            proxy.policy_decider = None;
        }
        proxy.execution_scope = Some(Arc::new(ExecutionScope {
            environment_id: environment_id.to_string(),
            execution_id: execution_id.to_string(),
            attribution_token,
            environment_policy,
            lifetime_tx,
            state: Arc::clone(&self.state),
        }));
        Ok(proxy)
    }
}
