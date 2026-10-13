//! 对位 codex 39f4277087 `otel/tests/suite/snapshot.rs`。
//! 裁点：锚点后两例 `manager_snapshot_metrics_collects_without_shutdown` /
//! `manager_turn_cost_records_microusd_metric` 依赖 `SessionTelemetry` 管理器面
//! （连带 codex-protocol `ThreadId`/`SessionSource` 与 `TelemetryAuthMode`），
//! nova 侧未镜像，本文件只移植不依赖该面的前两例。

use crate::harness::attributes_to_map;
use crate::harness::find_metric;
use nova_exec_server_otel::MetricsClient;
use nova_exec_server_otel::MetricsConfig;
use nova_exec_server_otel::Result;
use opentelemetry_sdk::metrics::InMemoryMetricExporter;
use opentelemetry_sdk::metrics::data::AggregatedMetrics;
use opentelemetry_sdk::metrics::data::MetricData;
use pretty_assertions::assert_eq;
use std::collections::BTreeMap;

#[test]
fn snapshot_collects_metrics_without_shutdown() -> Result<()> {
    let exporter = InMemoryMetricExporter::default();
    let config = MetricsConfig::in_memory(
        "test",
        "nova-cli",
        env!("CARGO_PKG_VERSION"),
        exporter.clone(),
    )
    .with_tag("service", "nova-cli")?
    .with_runtime_reader();
    let metrics = MetricsClient::new(config)?;

    metrics.counter(
        "nova.tool.call",
        /*inc*/ 1,
        &[("tool", "shell"), ("success", "true")],
    )?;

    let snapshot = metrics.snapshot()?;

    let metric = find_metric(&snapshot, "nova.tool.call").expect("counter metric missing");
    let attrs = match metric.data() {
        AggregatedMetrics::U64(data) => match data {
            MetricData::Sum(sum) => {
                let points: Vec<_> = sum.data_points().collect();
                assert_eq!(points.len(), 1);
                attributes_to_map(points[0].attributes())
            }
            _ => panic!("unexpected counter aggregation"),
        },
        _ => panic!("unexpected counter data type"),
    };

    let expected = BTreeMap::from([
        ("service".to_string(), "nova-cli".to_string()),
        ("success".to_string(), "true".to_string()),
        ("tool".to_string(), "shell".to_string()),
    ]);
    assert_eq!(attrs, expected);

    let finished = exporter
        .get_finished_metrics()
        .expect("finished metrics should be readable");
    assert!(finished.is_empty(), "expected no periodic exports yet");

    Ok(())
}

#[test]
fn observable_gauge_is_collected_on_every_delta_snapshot() -> Result<()> {
    let exporter = InMemoryMetricExporter::default();
    let config = MetricsConfig::in_memory("test", "nova-cli", env!("CARGO_PKG_VERSION"), exporter)
        .with_runtime_reader();
    let metrics = MetricsClient::new(config)?;
    metrics.register_observable_gauge_with_description(
        "nova.active",
        "Number of active operations.",
        || 1,
        &[("component", "test")],
    )?;

    for snapshot in [metrics.snapshot()?, metrics.snapshot()?] {
        let gauge = find_metric(&snapshot, "nova.active").expect("gauge metric missing");
        let point = match gauge.data() {
            AggregatedMetrics::I64(MetricData::Gauge(gauge)) => {
                gauge.data_points().next().expect("gauge point")
            }
            _ => panic!("unexpected gauge metric data type"),
        };
        assert_eq!(point.value(), 1);
        assert_eq!(
            attributes_to_map(point.attributes()),
            BTreeMap::from([("component".to_string(), "test".to_string())])
        );
    }

    metrics.shutdown()?;
    Ok(())
}
