use nova_exec_server_http_client::HttpClientFactory;
use nova_exec_server_http_client::OutboundProxyPolicy;
use pretty_assertions::assert_eq;

use super::PreparedEnvironmentManager;
use super::PreparedEnvironmentSource;
use crate::DefaultEnvironmentProvider;
use crate::LOCAL_ENVIRONMENT_ID;
use crate::REMOTE_ENVIRONMENT_ID;
use crate::client_api::DEFAULT_REMOTE_EXEC_SERVER_CONNECT_TIMEOUT;
use crate::client_api::ExecServerTransportParams;
use crate::environment_provider::EnvironmentDefault;
use crate::environment_provider::EnvironmentProviderSnapshot;

#[test]
fn prepared_remote_environment_is_detected_without_constructing_a_connection() {
    let prepared = PreparedEnvironmentManager {
        source: PreparedEnvironmentSource::Snapshot(EnvironmentProviderSnapshot {
            environments: vec![(
                REMOTE_ENVIRONMENT_ID.to_string(),
                ExecServerTransportParams::websocket_url(
                    "ws://username:password@executor.example/private?token=secret".to_string(),
                    DEFAULT_REMOTE_EXEC_SERVER_CONNECT_TIMEOUT,
                ),
            )],
            default: EnvironmentDefault::EnvironmentId(REMOTE_ENVIRONMENT_ID.to_string()),
            include_local: false,
        }),
    };

    assert!(prepared.default_environment_is_remote());

    let debug = format!("{prepared:?}");
    assert!(!debug.contains("username"));
    assert!(!debug.contains("password"));
    assert!(!debug.contains("executor.example"));
    assert!(!debug.contains("secret"));
}

#[test]
fn prepared_local_and_disabled_environments_are_not_remote() {
    let local = PreparedEnvironmentManager {
        source: PreparedEnvironmentSource::Snapshot(EnvironmentProviderSnapshot {
            environments: Vec::new(),
            default: EnvironmentDefault::EnvironmentId(LOCAL_ENVIRONMENT_ID.to_string()),
            include_local: true,
        }),
    };
    let disabled = PreparedEnvironmentManager {
        source: PreparedEnvironmentSource::Snapshot(EnvironmentProviderSnapshot {
            environments: Vec::new(),
            default: EnvironmentDefault::Disabled,
            include_local: false,
        }),
    };

    assert_eq!(
        [
            local.default_environment_is_remote(),
            disabled.default_environment_is_remote()
        ],
        [false, false]
    );
}

#[tokio::test]
async fn prepared_environment_manager_builds_with_the_explicit_http_policy() {
    let prepared = PreparedEnvironmentManager {
        source: PreparedEnvironmentSource::Snapshot(
            DefaultEnvironmentProvider::new(Some("ws://127.0.0.1:8765".to_string()))
                .snapshot_inner(),
        ),
    };
    let manager = prepared
        .build(
            /*local_runtime_paths*/ None,
            HttpClientFactory::new(OutboundProxyPolicy::RespectSystemProxy),
        )
        .expect("environment manager");

    assert_eq!(
        manager.default_environment_id(),
        Some(REMOTE_ENVIRONMENT_ID)
    );
    assert!(
        manager
            .default_environment()
            .expect("remote environment")
            .is_remote()
    );
    assert_eq!(
        manager.http_client_factory().outbound_proxy_policy(),
        OutboundProxyPolicy::RespectSystemProxy
    );
}
