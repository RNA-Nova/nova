//! Validates provisioning requests against the standard managed configuration layers.
//!
//! nova 适配说明（对位 codex `machine_policy.rs`）：
//! codex 版本在 impersonation 线程上加载 codex-config 的托管配置层栈
//! （`load_config_toml_with_layer_stack` + codex-cloud-config bundle +
//! codex-core 的 `bootstrap_auth_config`），再把
//! `ConfigRequirementsToml` 交给 `validate_requirements` 逐项裁决
//! （允许的沙箱实现、网络开关、local binding、HTTP/SOCKS 代理端口）。
//! nova 没有对应的托管配置体系（codex-config / codex-cloud-config /
//! codex-core 均未移植），没有任何 requirements 可供执行，因此校验恒为
//! 通过。未来 nova 落地托管配置批次时，应在此恢复
//! `validate_requirements` 的纯校验逻辑与 impersonation 加载结构。

use anyhow::Result;
use nova_exec_server_windows_sandbox::WindowsSandboxProvisioningSettings;
use nova_exec_server_windows_sandbox::WindowsSandboxProxyListeners;
use std::path::Path;
use windows_sys::Win32::Foundation::HANDLE;

pub(crate) fn validate_provisioning_settings(
    sandbox_home: &Path,
    settings: &WindowsSandboxProvisioningSettings,
    listeners: &WindowsSandboxProxyListeners,
    impersonation_token: HANDLE,
) -> Result<()> {
    let _ = (sandbox_home, settings, listeners, impersonation_token);
    Ok(())
}
