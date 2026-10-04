//! 客户端入站消息的共享上限，跨全部客户端传输生效（对位 codex 54487a5b61：
//! stdio/WebSocket 同阈值同拒绝语义；nova 未移植 relay/Noise relay 传输）。

use nova_exec_server_protocol::EXEC_CLOSED_METHOD;
use nova_exec_server_protocol::EXEC_EXITED_METHOD;
use nova_exec_server_protocol::EXEC_OUTPUT_DELTA_METHOD;
use nova_exec_server_protocol::FS_READ_STREAM_CHUNK_METHOD;
use nova_exec_server_protocol::HTTP_REQUEST_BODY_DELTA_METHOD;
use nova_exec_server_protocol::JSONRPCMessage;
use nova_exec_server_protocol::MAX_HTTP_BODY_DELTA_BYTES;

use crate::file_read::MAX_READ_STREAM_BLOCK_SIZE;

// A transport may materialize one larger frame before its JSON-RPC kind is known.
pub(crate) const MAX_CLIENT_INBOUND_REQUEST_LEN: usize = 8 * 1024;
// Streamed HTTP bodies carry up to 1 MiB before base64 and JSON-RPC framing.
pub(crate) const MAX_CLIENT_INBOUND_NOTIFICATION_LEN: usize = 2 * MAX_HTTP_BODY_DELTA_BYTES;
// nova 扩展数据面（上游豁免名单之外）：fs/readStream/chunk 单块解码上限
// MAX_READ_STREAM_BLOCK_SIZE（4 MiB），base64+分帧后约 5.6 MiB——按本协议
// 最大帧放宽（对位上游"数据面通知放宽"语义，阈值为 nova 特有扩展点）。
pub(crate) const MAX_FS_READ_STREAM_CHUNK_NOTIFICATION_LEN: usize =
    2 * MAX_READ_STREAM_BLOCK_SIZE;

pub(crate) fn client_inbound_message_exceeded_limit(
    message: Result<&JSONRPCMessage, &serde_json::Error>,
    encoded_len: usize,
    max_request_len: usize,
) -> Option<usize> {
    let max_len = match message {
        Ok(JSONRPCMessage::Notification(notification))
            if notification.method == FS_READ_STREAM_CHUNK_METHOD =>
        {
            MAX_FS_READ_STREAM_CHUNK_NOTIFICATION_LEN
        }
        Ok(JSONRPCMessage::Notification(notification))
            if matches!(
                notification.method.as_str(),
                EXEC_OUTPUT_DELTA_METHOD
                    | EXEC_EXITED_METHOD
                    | EXEC_CLOSED_METHOD
                    | HTTP_REQUEST_BODY_DELTA_METHOD
            ) =>
        {
            MAX_CLIENT_INBOUND_NOTIFICATION_LEN
        }
        Ok(JSONRPCMessage::Request(_)) | Ok(JSONRPCMessage::Notification(_)) | Err(_) => {
            max_request_len
        }
        Ok(JSONRPCMessage::Response(_)) | Ok(JSONRPCMessage::Error(_)) => return None,
    };
    (encoded_len > max_len).then_some(max_len)
}

#[cfg(test)]
#[path = "client_inbound_request_limit_tests.rs"]
mod tests;
