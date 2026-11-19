# nova-logging

Nova 的共享日志机制包。业务代码仍使用 stdlib `logging.getLogger(__name__)`；只有 CLI、Gateway 等组合根调用 `configure_logging()`，统一 contextvars、字段白名单、异常清洗、多出口过滤与异步输出。

本包不依赖任何 Nova 业务包。密钥清洗函数由组合根注入，日志不替代用户诊断、遥测或业务分析；OTel span、metrics 和 OTLP 归 `nova_telemetry`。

## 组合根装配

```python
import logging
from pathlib import Path

from nova_logging import LoggingSettings, SinkSettings, configure_logging

logging_runtime = configure_logging(
    LoggingSettings(
        sinks=(
            SinkSettings(
                level=logging.INFO,
                json_output=False,
                target_levels={"httpx": logging.WARNING},
            ),
            SinkSettings(
                level=logging.DEBUG,
                json_output=True,
                file_path=Path("logs/nova.jsonl"),
            ),
        ),
        queue_capacity=1000,
        redactor=my_secret_redactor,
    )
)
try:
    run_application()
    logging_runtime.flush(timeout=2)
finally:
    logging_runtime.close(timeout=5)
```

每个 `SinkSettings` 是一个独立出口，拥有自己的级别、logger 名最长前缀 `target_levels` 和输出格式。缺省配置是单个 INFO 级 JSON stdout；`stream` 可注入只实现 `write/flush` 的 `LogStream`，`file_path` 改用大小轮转文件，两者互斥。标准 stdout/stream 与文件出口都由包内受监督 handler 创建，stdlib 默认 `handleError` 不会绕过脱敏和统计。

- 文件默认 10 MiB、保留 5 份；POSIX 当前文件与轮转副本强制 `0600`，其他平台使用操作系统默认 ACL。
- 所有出口共享有界 `QueueHandler + QueueListener`。满队列丢弃新记录，不把慢磁盘压力回传给业务线程，也不递归再写“队列满”日志。
- `stats()` 返回不可变 `LoggingStats(dropped_records, sink_failures)` 快照，供组合根或后续遥测读取；写入、轮转与关闭失败都计数。
- sink 失败与入队失败只向 stderr 报异常类型，不回显原始记录或异常文案。
- 一个进程只允许一个活跃 runtime；上一代完全关闭前再次 `configure_logging()` 会响亮失败，防止 root handlers/level 交叉恢复。

## flush 与关停

`flush(timeout=...)` 在同一队列中插入屏障，回执表示此前接受的记录已经离开 sink。`timeout=None` 表示无限等待；超时只放弃等待，不撤销屏障或清空队列。

`close(timeout=5)` 先摘除 root 入口，再在后台线程排干并关闭出口。超时抛 `TimeoutError`，后台仍继续处理，可再次调用 `close()` 等待完成；`close(timeout=None)` 表示无限等待。后台线程为非 daemon，解释器也会等待它，因此阻塞 sink 仍可能挡住进程退出；强制退出由外层 watchdog 决定，不由日志包截断数据。关闭完成后 `close()` 与 `flush()` 都是幂等 no-op。

## 请求上下文

```python
from nova_logging import log_context

with log_context(requestId=request_id, provider="kimi-coding", model=model_id):
    logger.info("gateway.request.started")
```

上下文基于 `contextvars`，嵌套退出会恢复外层值，异步任务之间不串号；它在调用线程随记录入队，因此不会被 listener 线程的上下文覆盖。只接受 `requestId/sessionId/runId/provider/model/transport`，值必须为字符串或 `None`。重试次数、状态、错误分类和耗时通过 stdlib `extra` 传递：

```python
logger.info(
    "gateway.request.completed",
    extra={"status": 200, "durationMs": 12.5},
)
```

输出字段固定为 `ts/level/event/logger`、上下文字段、`retryAttempt/retriesTaken/status/errorKind/durationMs` 与异常/堆栈字段；其他 `extra` 一律丢弃。不要记录 Authorization、完整 headers、消息正文、工具参数、SSE delta 或原始请求/响应。

测试可使用 `structlog.testing.capture_logs(processors=[merge_contextvars])` 验证上下文，也可通过 `SinkSettings(stream=StringIO())` 检查完整队列、过滤、flush、背压、失败脱敏与关停管线。
