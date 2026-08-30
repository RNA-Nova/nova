# nova-executor-otel

OpenTelemetry 集成：日志/追踪/指标 exporter 装配、有界关闭（shutdown worker
带超时预算）、Statsig OTLP 导出、W3C trace context 传播与运行时指标汇总。

派生自 OpenAI Codex 的 `codex-otel`（Apache-2.0）；agent 业务指标目录按
nova 的纯度边界裁剪。
