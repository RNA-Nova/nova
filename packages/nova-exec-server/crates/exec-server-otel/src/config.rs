use std::collections::BTreeMap;
use std::collections::HashMap;
use std::path::PathBuf;

use nova_exec_server_utils_absolute_path::AbsolutePathBuf;
pub(crate) fn resolve_exporter(exporter: &OtelExporter) -> OtelExporter {
    exporter.clone()
}

/// Validates configured span attributes before they are attached to exported spans.
pub fn validate_span_attributes(attributes: &BTreeMap<String, String>) -> std::io::Result<()> {
    if attributes.keys().any(String::is_empty) {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "configured span attribute key must not be empty",
        ));
    }

    Ok(())
}

#[derive(Clone, Debug)]
pub struct OtelSettings {
    pub environment: String,
    pub service_name: String,
    pub service_version: String,
    pub exporter: OtelExporter,
    pub trace_exporter: OtelExporter,
    pub metrics_exporter: OtelExporter,
    pub runtime_metrics: bool,
    pub span_attributes: BTreeMap<String, String>,
    pub tracestate: BTreeMap<String, BTreeMap<String, String>>,
    /// 共享 HTTP 路由与网络策略工厂（OTLP  exporters 与 exec 流量同栈）。
    pub http_client_factory: nova_exec_server_http_client::HttpClientFactory,
}

#[derive(Clone, Debug, Default, serde::Serialize, serde::Deserialize)]
pub enum OtelHttpProtocol {
    /// HTTP protocol with binary protobuf
    #[default]
    Binary,
    /// HTTP protocol with JSON payload
    Json,
}

#[derive(Clone, Debug, Default, serde::Serialize, serde::Deserialize)]
pub struct OtelTlsConfig {
    pub ca_certificate: Option<AbsolutePathBuf>,
    pub client_certificate: Option<AbsolutePathBuf>,
    pub client_private_key: Option<AbsolutePathBuf>,
}

/// OTLP 出口形态（externally-tagged——对位 codex `OtelExporterKind` 的
/// 线上/配置形状：`{ OtlpGrpc = {...} }` / `{ OtlpHttp = {...} }` / `"None"`）。
#[derive(Clone, Debug, Default, serde::Serialize, serde::Deserialize)]
pub enum OtelExporter {
    #[default]
    None,
    OtlpGrpc {
        endpoint: String,
        #[serde(default)]
        headers: HashMap<String, String>,
        #[serde(default)]
        tls: Option<OtelTlsConfig>,
    },
    OtlpHttp {
        endpoint: String,
        #[serde(default)]
        headers: HashMap<String, String>,
        #[serde(default)]
        protocol: OtelHttpProtocol,
        #[serde(default)]
        tls: Option<OtelTlsConfig>,
    },
}

/// Resolved Statsig metrics settings that another process can use to recreate
/// the built-in metrics exporter configuration without receiving generic
/// exporter credentials in-process.
///
/// 对位 codex `StatsigMetricsSettings`（config.rs）。nova 未移植 Statsig 出口
/// 本体，本类型仅承载 Windows 沙箱 setup 链路的序列化传输。
#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct StatsigMetricsSettings {
    pub environment: String,
}

// ---------------------------------------------------------------------------
// config.toml 的 [otel] 段（对位 codex `OtelConfigToml` 的 metrics 子集）
// ---------------------------------------------------------------------------

/// `~/.nova/exec-server/config.toml` 的 `[otel]` 段。
///
/// 形状对位 codex `OtelConfigToml`。其余键（沙箱/网络/环境词汇）共存于同一文件，
/// 本结构只投影 `[otel]` 段。
#[derive(Clone, Debug, Default, serde::Deserialize)]
#[serde(default)]
pub struct OtelFileConfig {
    /// Mark metrics with environment (dev, staging, prod)。
    pub environment: Option<String>,
    /// metrics 出口（缺席 = 全静默 noop）。
    pub metrics_exporter: OtelExporter,
    /// logs 出口（缺席 = 不安装 logger layer）。
    pub exporter: OtelExporter,
    /// traces 出口（缺席 = 不安装 tracer provider）。
    pub trace_exporter: OtelExporter,
}

/// 从 executor 配置根装配遥测设置（对位 codex `build_provider` 的配置→装配形状）。
///
/// - 文件缺席 / 无 `[otel]` 段 / 无 metrics 出口 → 全静默 noop（`metrics_exporter=None`）；
/// - 配置根的其余段（sandbox/network/environments 词汇）不被本函数触碰。
pub fn load_otel_settings(executor_home: &std::path::Path) -> std::io::Result<OtelSettings> {
    let config = read_otel_file_config(executor_home)?;
    Ok(OtelSettings {
        environment: config
            .environment
            .and_then(|value| {
                let trimmed = value.trim().to_string();
                (!trimmed.is_empty()).then_some(trimmed)
            })
            .unwrap_or_else(|| "production".to_string()),
        service_name: "nova-exec-server".to_string(),
        service_version: env!("CARGO_PKG_VERSION").to_string(),
        exporter: config.exporter,
        trace_exporter: config.trace_exporter,
        metrics_exporter: config.metrics_exporter,
        runtime_metrics: false,
        span_attributes: BTreeMap::new(),
        tracestate: BTreeMap::new(),
        http_client_factory: nova_exec_server_http_client::HttpClientFactory::new(
            nova_exec_server_http_client::OutboundProxyPolicy::ReqwestDefault,
        ),
    })
}

fn read_otel_file_config(executor_home: &std::path::Path) -> std::io::Result<OtelFileConfig> {
    let path = executor_home.join("config.toml");
    let text = match std::fs::read_to_string(&path) {
        Ok(text) => text,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(OtelFileConfig::default());
        }
        Err(error) => return Err(error),
    };
    let document: toml::Value = toml::from_str(&text).map_err(|error| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!("failed to parse {}: {error}", path.display()),
        )
    })?;
    match document.get("otel") {
        Some(section) => section.clone().try_into().map_err(|error| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("invalid [otel] section in {}: {error}", path.display()),
            )
        }),
        None => Ok(OtelFileConfig::default()),
    }
}

#[cfg(test)]
mod otel_file_config_tests {
    use super::*;

    #[test]
    fn missing_home_means_silent_noop() {
        let temp = tempfile::tempdir().expect("tempdir");
        let settings = load_otel_settings(temp.path()).expect("load settings");
        assert!(matches!(settings.metrics_exporter, OtelExporter::None));
        assert_eq!(settings.service_name, "nova-exec-server");
        assert_eq!(settings.environment, "production");
    }

    #[test]
    fn grpc_metrics_section_maps_to_settings() {
        let temp = tempfile::tempdir().expect("tempdir");
        std::fs::write(
            temp.path().join("config.toml"),
            r#"
sandbox_mode = "workspace-write"

[otel]
environment = "staging"
metrics_exporter = { OtlpGrpc = { endpoint = "http://127.0.0.1:4317", headers = { authorization = "Bearer t" } } }
"#,
        )
        .expect("write config");
        let settings = load_otel_settings(temp.path()).expect("load settings");
        let OtelExporter::OtlpGrpc {
            endpoint, headers, ..
        } = &settings.metrics_exporter
        else {
            panic!("expected OtlpGrpc metrics exporter");
        };
        assert_eq!(endpoint, "http://127.0.0.1:4317");
        assert_eq!(headers.get("authorization").unwrap(), "Bearer t");
        assert_eq!(settings.environment, "staging");
        // 同文件的批次 1 词汇段不干扰 [otel] 投影
    }

    #[test]
    fn http_metrics_section_with_protocol() {
        let temp = tempfile::tempdir().expect("tempdir");
        std::fs::write(
            temp.path().join("config.toml"),
            r#"
[otel]
metrics_exporter = { OtlpHttp = { endpoint = "https://otel.example.com:4318", protocol = "Json" } }
"#,
        )
        .expect("write config");
        let settings = load_otel_settings(temp.path()).expect("load settings");
        let OtelExporter::OtlpHttp {
            endpoint, protocol, ..
        } = &settings.metrics_exporter
        else {
            panic!("expected OtlpHttp metrics exporter");
        };
        assert_eq!(endpoint, "https://otel.example.com:4318");
        assert!(matches!(protocol, OtelHttpProtocol::Json));
    }

    #[test]
    fn logs_and_traces_sections_map_to_settings() {
        let temp = tempfile::tempdir().expect("tempdir");
        std::fs::write(
            temp.path().join("config.toml"),
            r#"
[otel]
exporter = { OtlpHttp = { endpoint = "http://127.0.0.1:4318/v1/logs", protocol = "Json" } }
trace_exporter = { OtlpHttp = { endpoint = "http://127.0.0.1:4318/v1/traces", protocol = "Binary" } }
"#,
        )
        .expect("write config");
        let settings = load_otel_settings(temp.path()).expect("load settings");
        assert!(matches!(settings.exporter, OtelExporter::OtlpHttp { .. }));
        assert!(matches!(
            settings.trace_exporter,
            OtelExporter::OtlpHttp { .. }
        ));
    }
}
