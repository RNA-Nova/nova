use std::collections::HashMap;
use std::sync::Arc;

use nova_exec_server_file_system::WindowsSandboxSelection;
use nova_exec_server_network_proxy::CUSTOM_CA_ENV_KEYS;
use nova_exec_server_network_proxy::ManagedNetworkSandboxContext;
use nova_exec_server_network_proxy::ManagedProxyRouting;
use nova_exec_server_network_proxy::NetworkPolicyAuditObserver;
use nova_exec_server_network_proxy::NetworkPolicyDecider;
use nova_exec_server_network_proxy::NetworkProxy;
use nova_exec_server_network_proxy::NetworkProxyHandle;
use nova_exec_server_network_proxy::NetworkProxyState;
use nova_exec_server_network_proxy::RemoteNetworkProxyLaunchConfig;
use nova_exec_server_network_proxy::is_managed_mitm_ca_trust_bundle_path;
#[cfg(target_os = "windows")]
use nova_exec_server_network_proxy::strip_managed_proxy_env;
use nova_exec_server_protocol::JSONRPCErrorError;
use nova_exec_server_protocol_core::config_types::WindowsSandboxLevel;
use nova_exec_server_protocol_core::models::PermissionProfile;
use nova_exec_server_sandboxing::SandboxCommand;
use nova_exec_server_sandboxing::SandboxDirectSpawnTransformRequest;
use nova_exec_server_sandboxing::SandboxManager;
use nova_exec_server_sandboxing::SandboxTransformRequest;
use nova_exec_server_sandboxing::SandboxType;
use nova_exec_server_sandboxing::WindowsSandboxFilesystemOverrides;
use nova_exec_server_sandboxing::WindowsSandboxProxySettingsMode;
use nova_exec_server_sandboxing::WindowsSandboxSpawnRequest;
use nova_exec_server_sandboxing::resolve_windows_elevated_filesystem_overrides;
use nova_exec_server_sandboxing::resolve_windows_restricted_token_filesystem_overrides;
use nova_exec_server_sandboxing::windows_sandbox_uses_elevated_backend;
use nova_exec_server_sandboxing::with_managed_mitm_ca_readable_root;
#[cfg(windows)]
use nova_exec_server_shell_command::shell_detect::fallback_powershell_shell_for_windows_sandbox;
use nova_exec_server_utils_absolute_path::AbsolutePathBuf;
use nova_exec_server_utils_path_uri::PathUri;

use crate::ExecServerRuntimePaths;
#[cfg(unix)]
use crate::NOVA_EXEC_SERVER_ARG0_EXEC_HELPER_ARG1;
use crate::protocol::ExecParams;
use crate::rpc::internal_error;
use crate::rpc::invalid_params;
use crate::sandbox_selection::select_sandbox;
#[cfg(windows)]
use std::path::Path;

pub(crate) struct PreparedExecRequest {
    pub(crate) command: Vec<String>,
    pub(crate) cwd: AbsolutePathBuf,
    pub(crate) env: HashMap<String, String>,
    pub(crate) arg0: Option<String>,
    pub(crate) sandbox: SandboxType,
    pub(crate) network_proxy_handle: Option<NetworkProxyHandle>,
    windows_sandbox: Option<PreparedWindowsSandboxRequest>,
}

struct PreparedWindowsSandboxRequest {
    permission_profile: PermissionProfile,
    workspace_roots: Vec<AbsolutePathBuf>,
    windows_sandbox_level: WindowsSandboxLevel,
    proxy_enforced: bool,
    network_proxy_restricting_sid: Option<String>,
    proxy_settings_mode: WindowsSandboxProxySettingsMode,
    filesystem_overrides: Option<WindowsSandboxFilesystemOverrides>,
}

impl PreparedExecRequest {
    pub(crate) fn windows_sandbox_spawn_request(&self) -> Option<WindowsSandboxSpawnRequest<'_>> {
        self.windows_sandbox
            .as_ref()
            .map(|request| WindowsSandboxSpawnRequest {
                permission_profile: &request.permission_profile,
                workspace_roots: &request.workspace_roots,
                windows_sandbox_level: request.windows_sandbox_level,
                proxy_enforced: request.proxy_enforced,
                network_proxy_restricting_sid: request.network_proxy_restricting_sid.as_deref(),
                proxy_settings_mode: request.proxy_settings_mode,
                filesystem_overrides: request.filesystem_overrides.as_ref(),
            })
    }
}

pub(crate) async fn prepare_exec_request(
    params: &ExecParams,
    env: HashMap<String, String>,
    runtime_paths: Option<&ExecServerRuntimePaths>,
    network_policy_decider: Option<Arc<dyn NetworkPolicyDecider>>,
    network_policy_audit_observer: Option<NetworkPolicyAuditObserver>,
) -> Result<PreparedExecRequest, JSONRPCErrorError> {
    if let Some(sandbox) = params.sandbox.as_ref()
        && sandbox.windows_sandbox_selection == WindowsSandboxSelection::Mxc
    {
        // 对位 codex c379459bba 的入口检查：自定义 argv0 不支持；
        // 可用性以 MXC 的 PSEC create/close 探测为准。
        if params.arg0.is_some() {
            return Err(invalid_params(
                "MXC custom argv0 is not supported".to_owned(),
            ));
        }
        if !nova_exec_server_sandboxing::windows_mxc_available() {
            return Err(invalid_params(
                "native MXC is unavailable on this executor".to_owned(),
            ));
        }
    }
    #[cfg(target_os = "windows")]
    let mut env = env;
    #[cfg(target_os = "windows")]
    let network_proxy = if params.sandbox.is_none() {
        // Shared Windows ingress selects a route from the sandbox token's SID. Native launches
        // have no route SID, so leave them direct.
        if params.network_proxy.is_some() {
            strip_managed_proxy_env(&mut env);
        }
        None
    } else {
        params.network_proxy.as_ref()
    };
    #[cfg(not(target_os = "windows"))]
    let network_proxy = params.network_proxy.as_ref();

    let (env, managed_network, network_proxy_handle, network_proxy_restricting_sid) =
        prepare_managed_network(
            params,
            network_proxy,
            env,
            network_policy_decider,
            network_policy_audit_observer,
        )
        .await?;
    let Some(sandbox_context) = params.sandbox.as_ref() else {
        return Ok(PreparedExecRequest {
            command: params.argv.clone(),
            cwd: native_path(&params.cwd, "cwd")?,
            env,
            arg0: params.arg0.clone(),
            sandbox: SandboxType::None,
            network_proxy_handle,
            windows_sandbox: None,
        });
    };
    let runtime_paths = runtime_paths
        .ok_or_else(|| invalid_params("sandbox runtime paths are not configured".to_string()))?;
    // 对位 codex 841b5490b2：本机兼容校验在执法侧显式进行；permissions 已是
    // PermissionProfile，策略 cwd 必填（入口解析已归一）。
    sandbox_context
        .validate_file_system_paths_for_current_host()
        .map_err(|err| invalid_params(err.to_string()))?;
    let windows_sandbox_proxy_settings_mode = sandbox_context
        .windows_sandbox_proxy_settings_mode
        .unwrap_or_default();
    // TODO(nova): Transport permissions before orchestrator-local paths are materialized,
    // then resolve executor-local helper and workspace paths here.
    let permissions = sandbox_context.permissions.clone();
    let sandbox_policy_cwd = &sandbox_context.cwd;
    let native_sandbox_policy_cwd = native_path(sandbox_policy_cwd, "sandbox cwd")?;
    let native_workspace_roots = sandbox_context
        .workspace_roots
        .iter()
        .map(|root| native_path(root, "sandbox workspace root"))
        .collect::<Result<Vec<_>, _>>()?;
    let workspace_roots = native_workspace_roots.as_slice();
    let permissions = permissions.materialize_project_roots_with_workspace_roots(workspace_roots);
    let managed_mitm_ca_trust_bundle_path = managed_network.as_ref().and_then(|_| {
        CUSTOM_CA_ENV_KEYS.iter().find_map(|key| {
            let path = env.get(*key)?;
            if !is_managed_mitm_ca_trust_bundle_path(path) {
                return None;
            }
            AbsolutePathBuf::from_absolute_path(path).ok()
        })
    });
    let permissions = with_managed_mitm_ca_readable_root(
        permissions,
        managed_mitm_ca_trust_bundle_path.as_ref(),
        native_sandbox_policy_cwd.as_path(),
    );
    #[cfg(unix)]
    let (file_system_policy, network_policy) = permissions.to_runtime_permissions();
    #[cfg(unix)]
    let sandbox_helper_paths = params
        .arg0
        .iter()
        .map(|_| runtime_paths.executor_self_exe.clone())
        .collect::<Vec<_>>();
    // Bubblewrap launches the configured helper, which may re-enter this executable to apply
    // seccomp, so the outer filesystem sandbox must expose both paths.
    #[cfg(target_os = "linux")]
    let sandbox_helper_paths = {
        let mut sandbox_helper_paths = sandbox_helper_paths;
        if !sandbox_helper_paths.contains(&runtime_paths.executor_self_exe) {
            sandbox_helper_paths.push(runtime_paths.executor_self_exe.clone());
        }
        sandbox_helper_paths.extend(runtime_paths.executor_linux_sandbox_exe.iter().cloned());
        sandbox_helper_paths
    };
    #[cfg(unix)]
    let file_system_policy = file_system_policy
        .with_additional_readable_roots(native_sandbox_policy_cwd.as_path(), &sandbox_helper_paths);
    #[cfg(unix)]
    let permissions = PermissionProfile::from_runtime_permissions_with_enforcement(
        permissions.enforcement(),
        &file_system_policy,
        network_policy,
    );
    let sandbox_manager = SandboxManager::new();
    // 对位 codex d13aeb77ea：进程沙箱套用 NOVA_HOME symlink opt-out
    #[cfg(target_os = "macos")]
    let sandbox_manager = sandbox_manager
        .with_allowed_symlinked_nova_home(runtime_paths.allowed_symlinked_nova_home.clone());
    let (sandbox, windows_sandbox_level) = select_sandbox(
        &sandbox_manager,
        &permissions,
        sandbox_context,
        params.enforce_managed_network,
    );
    if sandbox == SandboxType::None {
        return Err(invalid_params(
            "sandbox intent cannot be enforced on this executor".to_string(),
        ));
    }
    let (program, args) = params
        .argv
        .split_first()
        .ok_or_else(|| invalid_params("argv must not be empty".to_string()))?;
    #[cfg(unix)]
    let (program, args) = params.arg0.as_ref().map_or_else(
        || (program.into(), args.to_vec()),
        |arg0| {
            let mut helper_args = Vec::with_capacity(params.argv.len() + 2);
            helper_args.push(NOVA_EXEC_SERVER_ARG0_EXEC_HELPER_ARG1.to_string());
            helper_args.push(arg0.clone());
            helper_args.extend(params.argv.iter().cloned());
            (
                runtime_paths
                    .executor_self_exe
                    .as_path()
                    .as_os_str()
                    .to_owned(),
                helper_args,
            )
        },
    );
    #[cfg(not(unix))]
    let (program, args) = (program.into(), args.to_vec());
    #[cfg(windows)]
    let program = if matches!(
        sandbox_context.windows_sandbox_selection,
        WindowsSandboxSelection::Elevated | WindowsSandboxSelection::Mxc
    ) && Path::new(&program).file_stem().is_some_and(|name| {
        name.eq_ignore_ascii_case("pwsh") || name.eq_ignore_ascii_case("powershell")
    }) && let Some(fallback) =
        fallback_powershell_shell_for_windows_sandbox(Path::new(&program))
    {
        // Remote controllers cannot resolve a sandbox-compatible shell on this host.
        fallback.shell_path.into_os_string()
    } else {
        program
    };
    let transform_request = SandboxDirectSpawnTransformRequest {
        workspace_roots,
        windows_sandbox_proxy_settings_mode,
        transform: SandboxTransformRequest {
            command: SandboxCommand {
                program,
                args,
                cwd: params.cwd.clone(),
                env,
                managed_network,
                additional_permissions: None,
            },
            permissions: &permissions,
            sandbox,
            enforce_managed_network: params.enforce_managed_network,
            environment_id: None,
            network: None,
            sandbox_policy_cwd,
            sandbox_exe: if cfg!(windows) {
                Some(runtime_paths.executor_self_exe.as_path())
            } else {
                runtime_paths.executor_linux_sandbox_exe.as_deref()
            },
            use_legacy_landlock: sandbox_context.use_legacy_landlock,
            windows_sandbox_level: windows_sandbox_level.unwrap_or(WindowsSandboxLevel::Disabled),
        },
    };
    let mut request = if sandbox == SandboxType::WindowsRestrictedToken {
        // The shared launcher invokes the native Windows session spawner directly.
        sandbox_manager.transform(transform_request.transform)
    } else {
        sandbox_manager.transform_for_direct_spawn(transform_request)
    }
    .map_err(|err| invalid_params(format!("failed to prepare process sandbox: {err}")))?;
    let windows_sandbox = if sandbox == SandboxType::WindowsRestrictedToken {
        let windows_sandbox_level = windows_sandbox_level.ok_or_else(|| {
            invalid_params("restricted token sandbox requires a sandbox level".to_string())
        })?;
        request.arg0 = params.arg0.clone();
        let proxy_enforced = params.enforce_managed_network;
        let use_elevated = windows_sandbox_uses_elevated_backend(windows_sandbox_level);
        let filesystem_overrides = if use_elevated {
            resolve_windows_elevated_filesystem_overrides(
                sandbox,
                &permissions,
                &native_sandbox_policy_cwd,
                use_elevated,
            )
        } else {
            resolve_windows_restricted_token_filesystem_overrides(
                sandbox,
                &permissions,
                &native_sandbox_policy_cwd,
                windows_sandbox_level,
            )
        }
        .map_err(|err| invalid_params(format!("failed to prepare process sandbox: {err}")))?;
        Some(PreparedWindowsSandboxRequest {
            permission_profile: permissions,
            workspace_roots: native_workspace_roots,
            windows_sandbox_level,
            proxy_enforced,
            network_proxy_restricting_sid,
            proxy_settings_mode: windows_sandbox_proxy_settings_mode,
            filesystem_overrides,
        })
    } else {
        None
    };
    Ok(PreparedExecRequest {
        command: request.command,
        cwd: native_path(&request.cwd, "cwd")?,
        env: request.env,
        arg0: request.arg0,
        sandbox: request.sandbox,
        network_proxy_handle,
        windows_sandbox,
    })
}

async fn prepare_managed_network(
    params: &ExecParams,
    network_proxy: Option<&RemoteNetworkProxyLaunchConfig>,
    env: HashMap<String, String>,
    network_policy_decider: Option<Arc<dyn NetworkPolicyDecider>>,
    network_policy_audit_observer: Option<NetworkPolicyAuditObserver>,
) -> Result<
    (
        HashMap<String, String>,
        Option<ManagedNetworkSandboxContext>,
        Option<NetworkProxyHandle>,
        Option<String>,
    ),
    JSONRPCErrorError,
> {
    let Some(network_proxy) = network_proxy.cloned() else {
        return Ok((env, params.managed_network.clone(), None, None));
    };
    // 对位 codex a5c15ab5c0：MXC 选择专用 loopback 监听（其策略按端口放行
    // loopback），其余选择共享 ingress。nova 尚无共享 ingress 实现——
    // SharedIngress 的 restricting SID 取自未移植裁点，行为与既有桩一致。
    let routing = if params.sandbox.as_ref().is_some_and(|sandbox| {
        sandbox.windows_sandbox_selection == WindowsSandboxSelection::Mxc
    }) {
        ManagedProxyRouting::DedicatedListeners
    } else {
        ManagedProxyRouting::SharedIngress
    };
    let mut state = NetworkProxyState::from_remote_launch_config(network_proxy)
        .map_err(|err| invalid_params(format!("invalid network proxy config: {err}")))?;
    if let Some(observer) = network_policy_audit_observer {
        state.set_policy_audit_observer(observer);
    }
    let mut builder = NetworkProxy::builder()
        .state(Arc::new(state))
        .managed_proxy_routing(routing);
    if let Some(network_policy_decider) = network_policy_decider {
        builder = builder.policy_decider_arc(network_policy_decider);
    }
    let proxy = builder
        .build()
        .await
        .map_err(|err| internal_error(format!("failed to build executor network proxy: {err}")))?;
    let handle = proxy
        .run()
        .await
        .map_err(|err| internal_error(format!("failed to start executor network proxy: {err}")))?;
    #[cfg(target_os = "windows")]
    let network_proxy_restricting_sid = if routing == ManagedProxyRouting::SharedIngress {
        Some(
            proxy
                .network_proxy_restricting_sid(/*environment_id*/ None)
                .ok_or_else(|| {
                    internal_error(
                        "managed Windows proxy route is missing its restricting SID".to_string(),
                    )
                })?,
        )
    } else {
        None
    };
    #[cfg(not(target_os = "windows"))]
    let network_proxy_restricting_sid = None;
    let prepared = proxy
        .prepare_for_optional_environment(env, /*environment_id*/ None)
        .map_err(|err| {
            internal_error(format!("failed to prepare executor network proxy: {err}"))
        })?;
    Ok((
        prepared.env,
        Some(prepared.sandbox_context),
        Some(handle),
        network_proxy_restricting_sid,
    ))
}

fn native_path(path: &PathUri, label: &str) -> Result<AbsolutePathBuf, JSONRPCErrorError> {
    path.to_abs_path().map_err(|err| {
        invalid_params(format!(
            "{label} URI `{path}` is not valid on this exec-server host: {err}"
        ))
    })
}

#[cfg(test)]
#[path = "process_sandbox_tests.rs"]
mod tests;
