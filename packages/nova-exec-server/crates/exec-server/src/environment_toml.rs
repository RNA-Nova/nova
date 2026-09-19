//! 环境注册表解析（对位 codex `environment_toml.rs`）。
//!
//! 词表差异（nova 定案，单文件层栈）：注册表条目不住独立的
//! `environments.toml`，而住 `<exec-server home>/config.toml` 顶层的
//! `default_environment` / `include_local` / `[[environments]]` 三键——与
//! py SDK `config.py` 的 `ExecutorConfig` 同形。条目字段与校验逻辑逐一对位
//! codex；顶层未知键放行（同文件还住 sandbox/otel 等其他段的词表），条目级
//! 未知键拒绝（codex `deny_unknown_fields` 语义保留）。

use std::collections::HashMap;
use std::collections::HashSet;
use std::path::Path;
use std::path::PathBuf;
use std::time::Duration;

use serde::Deserialize;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;

use crate::DefaultEnvironmentProvider;
use crate::EnvironmentProvider;
use crate::EnvironmentProviderFuture;
use crate::ExecServerError;
use crate::client_api::DEFAULT_REMOTE_EXEC_SERVER_CONNECT_TIMEOUT;
use crate::client_api::DEFAULT_REMOTE_EXEC_SERVER_INITIALIZE_TIMEOUT;
use crate::client_api::ExecServerTransportParams;
use crate::client_api::StdioExecServerCommand;
use crate::environment::LOCAL_ENVIRONMENT_ID;
use crate::environment_provider::EnvironmentDefault;
use crate::environment_provider::EnvironmentProviderSnapshot;

const CONFIG_TOML_FILE: &str = "config.toml";
const MAX_ENVIRONMENT_ID_LEN: usize = 64;

#[derive(Deserialize, Debug, Default)]
struct EnvironmentsToml {
    default_environment: Option<String>,
    include_local: Option<bool>,

    #[serde(default)]
    environments: Vec<EnvironmentToml>,
}

#[derive(Deserialize, Debug, Default, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct EnvironmentToml {
    id: String,
    url: Option<String>,
    program: Option<String>,
    args: Option<Vec<String>>,
    env: Option<HashMap<String, String>>,
    cwd: Option<PathBuf>,
    #[serde(default, with = "option_duration_secs")]
    connect_timeout_sec: Option<Duration>,
    #[serde(default, with = "option_duration_secs")]
    initialize_timeout_sec: Option<Duration>,
}

#[derive(Clone, Debug)]
struct TomlEnvironmentProvider {
    default: EnvironmentDefault,
    include_local: bool,
    environments: Vec<(String, ExecServerTransportParams)>,
}

impl TomlEnvironmentProvider {
    #[cfg(test)]
    fn new(config: EnvironmentsToml) -> Result<Self, ExecServerError> {
        Self::new_with_config_dir(config, /*config_dir*/ None)
    }

    fn new_with_config_dir(
        config: EnvironmentsToml,
        config_dir: Option<&Path>,
    ) -> Result<Self, ExecServerError> {
        let EnvironmentsToml {
            default_environment,
            include_local,
            environments,
        } = config;
        let include_local = include_local.unwrap_or(true);
        let mut ids = HashSet::new();
        if include_local {
            ids.insert(LOCAL_ENVIRONMENT_ID.to_string());
        }
        let mut parsed_environments = Vec::with_capacity(environments.len());
        for item in environments {
            let (id, transport) = parse_environment_toml(item, config_dir)?;
            if !ids.insert(id.clone()) {
                return Err(ExecServerError::Protocol(format!(
                    "environment id `{id}` is duplicated"
                )));
            }
            parsed_environments.push((id, transport));
        }
        let default =
            normalize_default_environment_id(default_environment.as_deref(), include_local, &ids)?;
        Ok(Self {
            default,
            include_local,
            environments: parsed_environments,
        })
    }

    async fn snapshot(&self) -> Result<EnvironmentProviderSnapshot, ExecServerError> {
        Ok(EnvironmentProviderSnapshot {
            environments: self.environments.clone(),
            default: self.default.clone(),
            include_local: self.include_local,
        })
    }
}

impl EnvironmentProvider for TomlEnvironmentProvider {
    fn snapshot(&self) -> EnvironmentProviderFuture<'_> {
        Box::pin(TomlEnvironmentProvider::snapshot(self))
    }
}

fn parse_environment_toml(
    item: EnvironmentToml,
    config_dir: Option<&Path>,
) -> Result<(String, ExecServerTransportParams), ExecServerError> {
    let EnvironmentToml {
        id,
        url,
        program,
        args,
        env,
        cwd,
        connect_timeout_sec,
        initialize_timeout_sec,
    } = item;
    validate_environment_id(&id)?;
    if program.is_none() && (args.is_some() || env.is_some() || cwd.is_some()) {
        return Err(ExecServerError::Protocol(format!(
            "environment `{id}` args, env, and cwd require program"
        )));
    }
    if url.is_none() && connect_timeout_sec.is_some() {
        return Err(ExecServerError::Protocol(format!(
            "environment `{id}` connect_timeout_sec requires url"
        )));
    }

    let connect_timeout = connect_timeout_sec.unwrap_or(DEFAULT_REMOTE_EXEC_SERVER_CONNECT_TIMEOUT);
    let initialize_timeout =
        initialize_timeout_sec.unwrap_or(DEFAULT_REMOTE_EXEC_SERVER_INITIALIZE_TIMEOUT);

    let transport_params = match (url, program) {
        (Some(url), None) => {
            let url = validate_websocket_url(url)?;
            ExecServerTransportParams::WebSocketUrl {
                websocket_url: url,
                connect_timeout,
                initialize_timeout,
            }
        }
        (None, Some(program)) => {
            let program = program.trim().to_string();
            if program.is_empty() {
                return Err(ExecServerError::Protocol(format!(
                    "environment `{id}` program cannot be empty"
                )));
            }
            let cwd = normalize_stdio_cwd(&id, cwd, config_dir)?;
            ExecServerTransportParams::StdioCommand {
                command: StdioExecServerCommand {
                    program,
                    args: args.unwrap_or_default(),
                    env: env.unwrap_or_default(),
                    cwd,
                },
                initialize_timeout,
            }
        }
        (None, None) | (Some(_), Some(_)) => {
            return Err(ExecServerError::Protocol(format!(
                "environment `{id}` must set exactly one of url or program"
            )));
        }
    };

    Ok((id, transport_params))
}

fn normalize_stdio_cwd(
    id: &str,
    cwd: Option<PathBuf>,
    config_dir: Option<&Path>,
) -> Result<Option<PathBuf>, ExecServerError> {
    let Some(cwd) = cwd else {
        return Ok(None);
    };
    if cwd.is_absolute() {
        return Ok(Some(cwd));
    }
    let Some(config_dir) = config_dir else {
        return Err(ExecServerError::Protocol(format!(
            "environment `{id}` cwd must be absolute"
        )));
    };
    Ok(Some(config_dir.join(cwd)))
}

pub(crate) fn environment_provider_from_config_home(
    config_home: &Path,
) -> Result<Box<dyn EnvironmentProvider>, ExecServerError> {
    let path = config_home.join(CONFIG_TOML_FILE);
    let Some(environments) = load_environments_toml(&path)? else {
        return Ok(Box::new(DefaultEnvironmentProvider::from_env()));
    };

    Ok(Box::new(TomlEnvironmentProvider::new_with_config_dir(
        environments,
        Some(config_home),
    )?))
}

fn normalize_default_environment_id(
    default: Option<&str>,
    include_local: bool,
    ids: &HashSet<String>,
) -> Result<EnvironmentDefault, ExecServerError> {
    let Some(default) = default.map(str::trim) else {
        return if include_local {
            Ok(EnvironmentDefault::EnvironmentId(
                LOCAL_ENVIRONMENT_ID.to_string(),
            ))
        } else {
            Ok(EnvironmentDefault::Disabled)
        };
    };
    if default.is_empty() {
        return Err(ExecServerError::Protocol(
            "default environment id cannot be empty".to_string(),
        ));
    }
    if !default.eq_ignore_ascii_case("none") && !ids.contains(default) {
        return Err(ExecServerError::Protocol(format!(
            "default environment `{default}` is not configured"
        )));
    }
    if default.eq_ignore_ascii_case("none") {
        Ok(EnvironmentDefault::Disabled)
    } else {
        Ok(EnvironmentDefault::EnvironmentId(default.to_string()))
    }
}

fn validate_environment_id(id: &str) -> Result<(), ExecServerError> {
    let trimmed_id = id.trim();
    if trimmed_id.is_empty() {
        return Err(ExecServerError::Protocol(
            "environment id cannot be empty".to_string(),
        ));
    }
    if trimmed_id != id {
        return Err(ExecServerError::Protocol(format!(
            "environment id `{id}` must not contain surrounding whitespace"
        )));
    }
    if id == LOCAL_ENVIRONMENT_ID || id.eq_ignore_ascii_case("none") {
        return Err(ExecServerError::Protocol(format!(
            "environment id `{id}` is reserved"
        )));
    }
    if id.len() > MAX_ENVIRONMENT_ID_LEN {
        return Err(ExecServerError::Protocol(format!(
            "environment id `{id}` cannot be longer than {MAX_ENVIRONMENT_ID_LEN} characters"
        )));
    }
    if !id
        .chars()
        .all(|ch| ch.is_ascii_alphanumeric() || ch == '-' || ch == '_')
    {
        return Err(ExecServerError::Protocol(format!(
            "environment id `{id}` must contain only ASCII letters, numbers, '-' or '_'"
        )));
    }
    Ok(())
}

fn validate_websocket_url(url: String) -> Result<String, ExecServerError> {
    let url = url.trim();
    if url.is_empty() {
        return Err(ExecServerError::Protocol(
            "environment url cannot be empty".to_string(),
        ));
    }
    if !url.starts_with("ws://") && !url.starts_with("wss://") {
        return Err(ExecServerError::Protocol(format!(
            "environment url `{url}` must use ws:// or wss://"
        )));
    }
    url.into_client_request().map_err(|err| {
        ExecServerError::Protocol(format!("environment url `{url}` is invalid: {err}"))
    })?;
    Ok(url.to_string())
}

/// Returns `None` when the config is missing; other I/O and parse failures remain errors.
fn load_environments_toml(path: &Path) -> Result<Option<EnvironmentsToml>, ExecServerError> {
    let contents = match std::fs::read_to_string(path) {
        Ok(contents) => contents,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(err) => {
            return Err(ExecServerError::Protocol(format!(
                "failed to read environment config `{}`: {err}",
                path.display()
            )));
        }
    };

    toml::from_str(&contents)
        .map_err(|err| {
            ExecServerError::Protocol(format!(
                "failed to parse environment config `{}`: {err}",
                path.display()
            ))
        })
        .map(Some)
}

mod option_duration_secs {
    use std::time::Duration;

    use serde::Deserialize;
    use serde::Deserializer;

    pub fn deserialize<'de, D>(deserializer: D) -> Result<Option<Duration>, D::Error>
    where
        D: Deserializer<'de>,
    {
        let secs = Option::<f64>::deserialize(deserializer)?;
        secs.map(|secs| Duration::try_from_secs_f64(secs).map_err(serde::de::Error::custom))
            .transpose()
    }
}

#[cfg(test)]
mod tests {
    use pretty_assertions::assert_eq;
    use tempfile::tempdir;

    use super::*;

    #[tokio::test]
    async fn toml_provider_includes_local_and_adds_configured_environments() {
        let provider = TomlEnvironmentProvider::new(EnvironmentsToml {
            default_environment: Some("ssh-dev".to_string()),
            include_local: None,
            environments: vec![
                EnvironmentToml {
                    id: "devbox".to_string(),
                    url: Some(" ws://127.0.0.1:8765 ".to_string()),
                    ..Default::default()
                },
                EnvironmentToml {
                    id: "ssh-dev".to_string(),
                    program: Some(" ssh ".to_string()),
                    args: Some(vec![
                        "dev".to_string(),
                        "nova-exec-server --listen stdio".to_string(),
                    ]),
                    env: Some(HashMap::from([(
                        "NOVA_LOG".to_string(),
                        "debug".to_string(),
                    )])),
                    ..Default::default()
                },
            ],
        })
        .expect("provider");

        let snapshot = provider.snapshot().await.expect("environments");
        let EnvironmentProviderSnapshot {
            environments,
            default,
            include_local,
        } = snapshot;
        let environment_ids: Vec<_> = environments
            .iter()
            .map(|(id, _environment)| id.as_str())
            .collect();
        assert_eq!(environment_ids, vec!["devbox", "ssh-dev"]);
        let environments: HashMap<_, _> = environments.into_iter().collect();

        assert!(include_local);
        assert!(!environments.contains_key(LOCAL_ENVIRONMENT_ID));
        assert!(matches!(
            &environments["devbox"],
            ExecServerTransportParams::WebSocketUrl { .. }
        ));
        assert!(matches!(
            &environments["ssh-dev"],
            ExecServerTransportParams::StdioCommand { .. }
        ));
        assert_eq!(
            default,
            EnvironmentDefault::EnvironmentId("ssh-dev".to_string())
        );
    }

    #[tokio::test]
    async fn toml_provider_default_omitted_selects_local() {
        let provider = TomlEnvironmentProvider::new(EnvironmentsToml::default()).expect("provider");
        let snapshot = provider.snapshot().await.expect("environments");

        assert!(snapshot.include_local);
        assert_eq!(
            snapshot.default,
            EnvironmentDefault::EnvironmentId(LOCAL_ENVIRONMENT_ID.to_string())
        );
    }

    #[tokio::test]
    async fn toml_provider_default_none_disables_default() {
        let provider = TomlEnvironmentProvider::new(EnvironmentsToml {
            default_environment: Some("none".to_string()),
            include_local: None,
            environments: Vec::new(),
        })
        .expect("provider");
        let snapshot = provider.snapshot().await.expect("environments");

        assert!(snapshot.include_local);
        assert_eq!(snapshot.default, EnvironmentDefault::Disabled);
    }

    #[tokio::test]
    async fn toml_provider_can_disable_local_environment() {
        let provider = TomlEnvironmentProvider::new(EnvironmentsToml {
            default_environment: Some("ssh-dev".to_string()),
            include_local: Some(false),
            environments: vec![EnvironmentToml {
                id: "ssh-dev".to_string(),
                program: Some("ssh".to_string()),
                ..Default::default()
            }],
        })
        .expect("provider");
        let snapshot = provider.snapshot().await.expect("environments");

        assert!(!snapshot.include_local);
        assert_eq!(
            snapshot.default,
            EnvironmentDefault::EnvironmentId("ssh-dev".to_string())
        );
    }

    #[tokio::test]
    async fn toml_provider_without_local_and_default_omitted_disables_default() {
        let provider = TomlEnvironmentProvider::new(EnvironmentsToml {
            include_local: Some(false),
            ..Default::default()
        })
        .expect("provider");
        let snapshot = provider.snapshot().await.expect("environments");

        assert!(!snapshot.include_local);
        assert_eq!(snapshot.default, EnvironmentDefault::Disabled);
    }

    #[test]
    fn toml_provider_rejects_local_default_when_local_is_disabled() {
        let err = TomlEnvironmentProvider::new(EnvironmentsToml {
            default_environment: Some(LOCAL_ENVIRONMENT_ID.to_string()),
            include_local: Some(false),
            environments: Vec::new(),
        })
        .expect_err("local default without local environment should fail");

        assert_eq!(
            err.to_string(),
            "exec-server protocol error: default environment `local` is not configured"
        );
    }

    #[test]
    fn toml_provider_rejects_invalid_environments() {
        let cases = [
            (
                EnvironmentToml {
                    id: "local".to_string(),
                    url: Some("ws://127.0.0.1:8765".to_string()),
                    ..Default::default()
                },
                "environment id `local` is reserved",
            ),
            (
                EnvironmentToml {
                    id: " devbox ".to_string(),
                    url: Some("ws://127.0.0.1:8765".to_string()),
                    ..Default::default()
                },
                "environment id ` devbox ` must not contain surrounding whitespace",
            ),
            (
                EnvironmentToml {
                    id: "dev box".to_string(),
                    url: Some("ws://127.0.0.1:8765".to_string()),
                    ..Default::default()
                },
                "environment id `dev box` must contain only ASCII letters, numbers, '-' or '_'",
            ),
            (
                EnvironmentToml {
                    id: "devbox".to_string(),
                    url: Some("http://127.0.1:8765".to_string()),
                    ..Default::default()
                },
                "environment url `http://127.0.1:8765` must use ws:// or wss://",
            ),
            (
                EnvironmentToml {
                    id: "devbox".to_string(),
                    url: Some("ws://127.0.0.1:8765".to_string()),
                    program: Some("nova".to_string()),
                    ..Default::default()
                },
                "environment `devbox` must set exactly one of url or program",
            ),
            (
                EnvironmentToml {
                    id: "devbox".to_string(),
                    program: Some(" ".to_string()),
                    ..Default::default()
                },
                "environment `devbox` program cannot be empty",
            ),
            (
                EnvironmentToml {
                    id: "devbox".to_string(),
                    args: Some(Vec::new()),
                    ..Default::default()
                },
                "environment `devbox` args, env, and cwd require program",
            ),
            (
                EnvironmentToml {
                    id: "ssh-dev".to_string(),
                    program: Some("ssh".to_string()),
                    connect_timeout_sec: Some(Duration::from_secs(1)),
                    ..Default::default()
                },
                "environment `ssh-dev` connect_timeout_sec requires url",
            ),
        ];

        for (item, expected) in cases {
            let err = TomlEnvironmentProvider::new(EnvironmentsToml {
                default_environment: None,
                include_local: None,
                environments: vec![item],
            })
            .expect_err("invalid item should fail");

            assert_eq!(
                err.to_string(),
                format!("exec-server protocol error: {expected}")
            );
        }
    }

    #[test]
    fn toml_provider_resolves_relative_stdio_cwd_from_config_dir() {
        let config_dir = tempdir().expect("tempdir");
        let provider = TomlEnvironmentProvider::new_with_config_dir(
            EnvironmentsToml {
                default_environment: None,
                include_local: None,
                environments: vec![EnvironmentToml {
                    id: "ssh-dev".to_string(),
                    program: Some("ssh".to_string()),
                    cwd: Some(PathBuf::from("workspace")),
                    ..Default::default()
                }],
            },
            Some(config_dir.path()),
        )
        .expect("provider");

        let ExecServerTransportParams::StdioCommand {
            command,
            initialize_timeout,
        } = &provider.environments[0].1
        else {
            panic!("expected stdio transport");
        };
        assert_eq!(
            command,
            &StdioExecServerCommand {
                program: "ssh".to_string(),
                args: Vec::new(),
                env: HashMap::new(),
                cwd: Some(config_dir.path().join("workspace")),
            }
        );
        assert_eq!(
            *initialize_timeout,
            DEFAULT_REMOTE_EXEC_SERVER_INITIALIZE_TIMEOUT
        );
    }

    #[test]
    fn toml_provider_parses_configured_transport_timeouts() {
        let provider = TomlEnvironmentProvider::new(EnvironmentsToml {
            default_environment: None,
            include_local: None,
            environments: vec![
                EnvironmentToml {
                    id: "devbox".to_string(),
                    url: Some("ws://127.0.0.1:8765".to_string()),
                    connect_timeout_sec: Some(Duration::from_secs(12)),
                    initialize_timeout_sec: Some(Duration::from_secs(34)),
                    ..Default::default()
                },
                EnvironmentToml {
                    id: "ssh-dev".to_string(),
                    program: Some("ssh".to_string()),
                    initialize_timeout_sec: Some(Duration::from_secs(56)),
                    ..Default::default()
                },
            ],
        })
        .expect("provider");

        let ExecServerTransportParams::WebSocketUrl {
            websocket_url,
            connect_timeout,
            initialize_timeout,
        } = &provider.environments[0].1
        else {
            panic!("expected websocket transport");
        };
        assert_eq!(websocket_url, "ws://127.0.0.1:8765");
        assert_eq!(*connect_timeout, Duration::from_secs(12));
        assert_eq!(*initialize_timeout, Duration::from_secs(34));

        let ExecServerTransportParams::StdioCommand {
            command,
            initialize_timeout,
        } = &provider.environments[1].1
        else {
            panic!("expected stdio transport");
        };
        assert_eq!(
            command,
            &StdioExecServerCommand {
                program: "ssh".to_string(),
                args: Vec::new(),
                env: HashMap::new(),
                cwd: None,
            }
        );
        assert_eq!(*initialize_timeout, Duration::from_secs(56));
    }

    #[test]
    fn toml_provider_rejects_relative_stdio_cwd_without_config_dir() {
        let err = TomlEnvironmentProvider::new(EnvironmentsToml {
            default_environment: None,
            include_local: None,
            environments: vec![EnvironmentToml {
                id: "ssh-dev".to_string(),
                program: Some("ssh".to_string()),
                cwd: Some(PathBuf::from("workspace")),
                ..Default::default()
            }],
        })
        .expect_err("relative cwd without config dir should fail");

        assert_eq!(
            err.to_string(),
            "exec-server protocol error: environment `ssh-dev` cwd must be absolute"
        );
    }

    #[test]
    fn toml_provider_rejects_duplicate_ids() {
        let err = TomlEnvironmentProvider::new(EnvironmentsToml {
            default_environment: None,
            include_local: None,
            environments: vec![
                EnvironmentToml {
                    id: "devbox".to_string(),
                    url: Some("ws://127.0.0.1:8765".to_string()),
                    ..Default::default()
                },
                EnvironmentToml {
                    id: "devbox".to_string(),
                    program: Some("nova".to_string()),
                    ..Default::default()
                },
            ],
        })
        .expect_err("duplicate id should fail");

        assert_eq!(
            err.to_string(),
            "exec-server protocol error: environment id `devbox` is duplicated"
        );
    }

    #[test]
    fn toml_provider_rejects_overlong_id() {
        let id = "a".repeat(MAX_ENVIRONMENT_ID_LEN + 1);
        let err = TomlEnvironmentProvider::new(EnvironmentsToml {
            default_environment: None,
            include_local: None,
            environments: vec![EnvironmentToml {
                id: id.clone(),
                url: Some("ws://127.0.0.1:8765".to_string()),
                ..Default::default()
            }],
        })
        .expect_err("overlong id should fail");

        assert_eq!(
            err.to_string(),
            format!(
                "exec-server protocol error: environment id `{id}` cannot be longer than {MAX_ENVIRONMENT_ID_LEN} characters"
            )
        );
    }

    #[test]
    fn toml_provider_rejects_unknown_default() {
        let err = TomlEnvironmentProvider::new(EnvironmentsToml {
            default_environment: Some("missing".to_string()),
            include_local: None,
            environments: Vec::new(),
        })
        .expect_err("unknown default should fail");

        assert_eq!(
            err.to_string(),
            "exec-server protocol error: default environment `missing` is not configured"
        );
    }

    #[test]
    fn load_environments_toml_reads_root_environment_list() {
        let config_home = tempdir().expect("tempdir");
        let path = config_home.path().join(CONFIG_TOML_FILE);
        std::fs::write(
            &path,
            r#"
default_environment = "ssh-dev"
include_local = false

[[environments]]
id = "devbox"
url = "ws://127.0.0.1:4512"
connect_timeout_sec = 12.0
initialize_timeout_sec = 34.0

[[environments]]
id = "ssh-dev"
program = "ssh"
args = ["dev", "nova-exec-server --listen stdio"]
cwd = "/tmp"
[environments.env]
NOVA_LOG = "debug"
"#,
        )
        .expect("write config.toml");

        let environments = load_environments_toml(&path)
            .expect("config.toml")
            .expect("config.toml should exist");

        assert_eq!(environments.default_environment.as_deref(), Some("ssh-dev"));
        assert_eq!(environments.include_local, Some(false));
        assert_eq!(environments.environments.len(), 2);
        assert_eq!(
            environments.environments[0],
            EnvironmentToml {
                id: "devbox".to_string(),
                url: Some("ws://127.0.0.1:4512".to_string()),
                connect_timeout_sec: Some(Duration::from_secs(12)),
                initialize_timeout_sec: Some(Duration::from_secs(34)),
                ..Default::default()
            }
        );
        assert_eq!(
            environments.environments[1],
            EnvironmentToml {
                id: "ssh-dev".to_string(),
                program: Some("ssh".to_string()),
                args: Some(vec![
                    "dev".to_string(),
                    "nova-exec-server --listen stdio".to_string(),
                ]),
                env: Some(HashMap::from([(
                    "NOVA_LOG".to_string(),
                    "debug".to_string(),
                )])),
                cwd: Some(PathBuf::from("/tmp")),
                ..Default::default()
            }
        );
    }

    #[test]
    fn load_environments_toml_ignores_other_sections_but_rejects_entry_unknown_fields() {
        let config_home = tempdir().expect("tempdir");
        // 顶层未知键属于其他配置段（sandbox/otel 等），放行
        let path = config_home.path().join(CONFIG_TOML_FILE);
        std::fs::write(&path, "sandbox_mode = \"read-only\"\n").expect("write config.toml");
        assert!(
            load_environments_toml(&path)
                .expect("other sections should parse")
                .is_some()
        );

        // 条目级未知键拒绝
        let path = config_home.path().join("config-entry-unknown.toml");
        std::fs::write(
            &path,
            r#"
[[environments]]
id = "devbox"
url = "ws://127.0.0.1:4512"
unknown = true
"#,
        )
        .expect("write config.toml");
        let err = load_environments_toml(&path).expect_err("unknown entry field should fail");
        assert!(
            err.to_string().contains("unknown field `unknown`"),
            "expected `{err}` to contain unknown field"
        );
    }

    #[test]
    fn toml_provider_rejects_malformed_websocket_url() {
        let err = TomlEnvironmentProvider::new(EnvironmentsToml {
            default_environment: None,
            include_local: None,
            environments: vec![EnvironmentToml {
                id: "devbox".to_string(),
                url: Some("ws://".to_string()),
                ..Default::default()
            }],
        })
        .expect_err("malformed websocket url should fail");

        assert!(
            err.to_string()
                .contains("environment url `ws://` is invalid"),
            "expected malformed URL error, got `{err}`"
        );
    }

    #[tokio::test]
    async fn environment_provider_from_config_home_uses_present_environments_file() {
        let config_home = tempdir().expect("tempdir");
        std::fs::write(
            config_home.path().join(CONFIG_TOML_FILE),
            r#"
default_environment = "none"
include_local = false
"#,
        )
        .expect("write config.toml");

        let provider =
            environment_provider_from_config_home(config_home.path()).expect("environment provider");

        let snapshot = provider.snapshot().await.expect("environments");
        let environment_ids: Vec<_> = snapshot
            .environments
            .into_iter()
            .map(|(id, _environment)| id)
            .collect();

        assert!(!snapshot.include_local);
        assert!(!environment_ids.contains(&LOCAL_ENVIRONMENT_ID.to_string()));
        assert_eq!(snapshot.default, EnvironmentDefault::Disabled);
    }

    #[tokio::test]
    async fn environment_provider_from_config_home_falls_back_when_file_is_missing() {
        let config_home = tempdir().expect("tempdir");

        let provider =
            environment_provider_from_config_home(config_home.path()).expect("environment provider");

        let snapshot = provider.snapshot().await.expect("environments");
        let environment_ids: Vec<_> = snapshot
            .environments
            .into_iter()
            .map(|(id, _environment)| id)
            .collect();

        assert!(snapshot.include_local);
        assert!(!environment_ids.contains(&LOCAL_ENVIRONMENT_ID.to_string()));
        assert_eq!(
            snapshot.default,
            EnvironmentDefault::EnvironmentId(LOCAL_ENVIRONMENT_ID.to_string())
        );
    }
}
