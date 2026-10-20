"""入站闸门：JSON 解析防线、畸形判定与按类别的入站限流（WS/stdio 共用）"""

from __future__ import annotations

import json
from typing import Any

from nova_protocol import (
    FS_READ_STREAM_CHUNK,
    HTTP_REQUEST_BODY_DELTA,
    PROCESS_CLOSED,
    PROCESS_EXITED,
    PROCESS_OUTPUT,
)

# ---------------------------------------------------------------------------
# 客户端入站限流（PROTOCOL v1.7，对位 Rust client_inbound_request_limit.rs）
# ---------------------------------------------------------------------------

#: 反向请求 / 未知通知 / 畸形消息的单帧上限（超限即断开连接）
MAX_CLIENT_INBOUND_REQUEST_LEN = 8 * 1024
#: 数据面通知（process/output|exited|closed、http/request/bodyDelta）的放宽档
MAX_CLIENT_INBOUND_NOTIFICATION_LEN = 2 * 1024 * 1024
#: nova 扩展数据面 fs/readStream/chunk 的放宽档（协议最大块 4MiB +
#: base64/分帧余量，对位 Rust MAX_FS_READ_STREAM_CHUNK_NOTIFICATION_LEN）
MAX_FS_READ_STREAM_CHUNK_NOTIFICATION_LEN = 8 * 1024 * 1024

#: 数据面通知放宽档的方法名单（对位 Rust 的豁免 matches!）
_DATA_PLANE_NOTIFICATION_METHODS = frozenset(
    {PROCESS_OUTPUT, PROCESS_EXITED, PROCESS_CLOSED, HTTP_REQUEST_BODY_DELTA}
)

#: 反向请求满员等路径的 fail-closed 拒决原因（对位 Rust NETWORK_POLICY_DENIAL_REASON）
NETWORK_POLICY_DENIAL_REASON = "not_allowed"

#: 入站消息 JSON 值节点预算（对位 Rust MAX_JSONRPC_VALUE_NODES，
#: exec-server-protocol rpc.rs）：根 + 每个数组元素/对象值各记一（键不计）。
#: 满档 fs/walk 响应（5 万条目）约需 15 万节点——256K 留足头部，又挡住
#: "紧凑字节膨胀成百万堆值"的放大攻击。Rust 在解析途中计费；py 在解析后
#: 显式栈遍历计费——消息字节已先受传输层单帧上限约束，瞬时内存有界
MAX_JSONRPC_VALUE_NODES = 256 * 1024


def _reject_json_constant(value: str) -> None:
    """json.loads parse_constant 钩子：NaN/Infinity/-Infinity 非合法 JSON
    （serde_json 词法层即拒收；py 的 json 默认放行，对位拒掉）"""
    raise ValueError(f"invalid JSON constant: {value}")


def _object_pairs_reject_duplicates(pairs: list) -> dict:
    """json.loads object_pairs_hook：对位 Rust BoundedValueVisitor::visit_map
    的重复键拒收（任意嵌套层；py 的 json 默认 last-wins 静默吞掉）"""
    obj: dict = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON object key `{key}`")
        obj[key] = value
    return obj


def _json_nodes_within_budget(value: Any) -> bool:
    """节点预算检查（显式栈遍历——不递归，深嵌套不触 Python 递归上限）"""
    remaining = MAX_JSONRPC_VALUE_NODES
    stack = [value]
    while stack:
        node = stack.pop()
        remaining -= 1
        if remaining < 0:
            return False
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return True


def _is_valid_request_id(value: Any) -> bool:
    """对位 Rust RequestId（untagged String | Integer(i64)）的线形校验"""
    if isinstance(value, str):
        return True
    if isinstance(value, bool):  # bool 是 int 子类——JSON true/false 不是合法 id
        return False
    return isinstance(value, int) and -(2**63) <= value < 2**63


def _is_malformed_message(message: Any) -> bool:
    """畸形消息判定——对位 Rust JSONRPCMessage 自定义反序列化（
    exec-server-protocol rpc.rs）的失败面，serde 拒收 = 连接级致命：

    - 非 JSON 对象
    - method 非字符串；method+id 组合中 id 非法（含 null——Rust 按
      contains_key 判型，`id: null` 走 Request 反序列化即失败）
    - 无 method 有 result：须带合法 id（Response）
    - 无 method 无 result：须为完整 error 形态（Error），否则拒收
    """
    if not isinstance(message, dict):
        return True
    if "method" in message:
        if not isinstance(message["method"], str):
            return True
        return "id" in message and not _is_valid_request_id(message["id"])
    if "result" in message:
        return not _is_valid_request_id(message.get("id"))
    error = message.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    return not (
        _is_valid_request_id(message.get("id"))
        and isinstance(error, dict)
        and isinstance(code, int)
        and not isinstance(code, bool)
        # serde i64 derive 溢出即畸形（与 _is_valid_request_id 同纪律）
        and -(2**63) <= code < 2**63
        and isinstance(error.get("message"), str)
    )


def _inbound_message_limit(message: dict) -> int | None:
    """按消息类别返回入站字节上限（对位 Rust
    client_inbound_message_exceeded_limit 的分档；None = 响应/错误——
    不受类别限流，仍受传输层单消息上限约束）"""
    method = message.get("method")
    if method is None:
        return None
    if message.get("id") is not None:
        return MAX_CLIENT_INBOUND_REQUEST_LEN
    if method == FS_READ_STREAM_CHUNK:
        return MAX_FS_READ_STREAM_CHUNK_NOTIFICATION_LEN
    if method in _DATA_PLANE_NOTIFICATION_METHODS:
        return MAX_CLIENT_INBOUND_NOTIFICATION_LEN
    return MAX_CLIENT_INBOUND_REQUEST_LEN


def _gate_inbound_message(
    payload: bytes | bytearray,
) -> tuple[dict | None, str | None]:
    """入站消息统一闸门（WS/stdio 共用，对位 Rust connection.rs 读循环的
    parse + client_inbound_message_exceeded_limit 组合）。

    返回 `(message, None)` = 放行分发；`(None, reason)` = 连接级致命
    （断开+恢复）。顺序对位 Rust：先类别限流（畸形消息按 8KiB 档
    fail-closed），再畸形判定。
    """
    encoded_len = len(payload)
    parse_error: Exception | None = None
    try:
        message: Any = json.loads(
            payload,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_object_pairs_reject_duplicates,
        )
    except (json.JSONDecodeError, ValueError, RecursionError) as e:
        # JSONDecodeError = 语法层；ValueError = 重复键/非法常量钩子拒收；
        # RecursionError = 超深嵌套（serde_json 自带递归上限；py 靠 json
        # 扫描器的递归保护兜底）——同为连接级致命
        message = None
        parse_error = e
    if message is not None and not _json_nodes_within_budget(message):
        return None, (
            "JSON-RPC message from exec-server exceeds the limit of "
            f"{MAX_JSONRPC_VALUE_NODES} JSON values"
        )
    malformed = message is None or _is_malformed_message(message)
    limit = (
        MAX_CLIENT_INBOUND_REQUEST_LEN if malformed else _inbound_message_limit(message)
    )
    if limit is not None and encoded_len > limit:
        return None, (
            "JSON-RPC message from exec-server exceeds maximum length of "
            f"{limit} bytes"
        )
    if message is None:
        return None, (
            f"failed to parse JSON-RPC message from exec-server: {parse_error}"
        )
    if malformed:
        return None, "malformed JSON-RPC message from exec-server"
    return message, None
