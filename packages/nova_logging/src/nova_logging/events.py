"""日志事件字段白名单；上下文、结构化元数据与出口共用同一词汇。"""

CONTEXT_FIELDS = frozenset(
    {
        "requestId",
        "sessionId",
        "runId",
        "provider",
        "model",
        "transport",
    }
)
METADATA_FIELDS = CONTEXT_FIELDS | frozenset(
    {
        "retryAttempt",
        "retriesTaken",
        "status",
        "errorKind",
        "durationMs",
    }
)
OUTPUT_FIELDS = frozenset(
    {
        "ts",
        "level",
        "event",
        "logger",
        "requestId",
        "sessionId",
        "runId",
        "provider",
        "model",
        "transport",
        "retryAttempt",
        "retriesTaken",
        "status",
        "errorKind",
        "durationMs",
        "exception",
        "stack",
    }
)

__all__ = ["CONTEXT_FIELDS", "METADATA_FIELDS", "OUTPUT_FIELDS"]
