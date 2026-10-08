use std::io;

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD;
use nova_exec_server_protocol::JSONRPCErrorError;

use crate::CopyOptions;
use crate::CreateDirectoryOptions;
use crate::ExecServerRuntimePaths;
use crate::ExecutorFileSystem;
use crate::GetMetadataOptions;
use crate::ReadFileOptions;
use crate::RemoveOptions;
use crate::WriteFileOptions;
use crate::file_handle::DEFAULT_READ_STREAM_BLOCK_SIZE;
use crate::file_handle::FileHandleManager;
use crate::file_handle::MAX_READ_STREAM_BLOCK_SIZE;
use crate::file_handle::stream_file_blocks;
use crate::local_file_system::LocalFileSystem;
use crate::regular_file::OpenMode;
use crate::protocol::FS_READ_DIRECTORY_METHOD;
use crate::protocol::FS_WRITE_FILE_METHOD;
use crate::protocol::FsCanonicalizeParams;
use crate::protocol::FsCanonicalizeResponse;
use crate::protocol::FsCloseParams;
use crate::protocol::FsCloseResponse;
use crate::protocol::FsCopyParams;
use crate::protocol::FsCopyResponse;
use crate::protocol::FsCreateDirectoryParams;
use crate::protocol::FsCreateDirectoryResponse;
use crate::protocol::FsGetMetadataParams;
use crate::protocol::FsGetMetadataResponse;
use crate::protocol::FsOpenParams;
use crate::protocol::FsOpenResponse;
use crate::protocol::FsReadBlockParams;
use crate::protocol::FsReadBlockResponse;
use crate::protocol::FsReadDirectoryEntry;
use crate::protocol::FsReadDirectoryParams;
use crate::protocol::FsReadDirectoryResponse;
use crate::protocol::FsReadFileParams;
use crate::protocol::FsReadFileResponse;
use crate::protocol::FsReadStreamChunkNotification;
use crate::protocol::FsReadStreamDoneNotification;
use crate::protocol::FsReadStreamParams;
use crate::protocol::FsReadStreamResponse;
use crate::protocol::FsRemoveParams;
use crate::protocol::FsRemoveResponse;
use crate::protocol::FsWalkParams;
use crate::protocol::FsWalkResponse;
use crate::protocol::FsWriteBlockParams;
use crate::protocol::FsWriteBlockResponse;
use crate::protocol::FsWriteFileParams;
use crate::protocol::FsWriteFileResponse;
use crate::protocol::FsWriteStreamChunkNotification;
use crate::protocol::FsWriteStreamDoneParams;
use crate::protocol::FsWriteStreamDoneResponse;
use crate::protocol::FsWriteStreamParams;
use crate::protocol::FsWriteStreamResponse;
use crate::rpc::RpcNotificationSender;
use crate::rpc::internal_error;
use crate::rpc::invalid_request;
use crate::rpc::not_found;

const MAX_FILE_HANDLE_ID_BYTES: usize = 32;
const MAX_FILE_WRITE_HANDLE_ID_BYTES: usize = 32;
// Each read-directory entry needs four JSON values. Keep same-version
// producers comfortably below the shared 256K-value decoder budget.
const MAX_READ_DIRECTORY_ENTRIES: usize = 50_000;

#[derive(Clone)]
pub(crate) struct FileSystemHandler {
    file_system: LocalFileSystem,
    file_handles: FileHandleManager,
    notifications: Option<RpcNotificationSender>,
}

impl FileSystemHandler {
    pub(crate) fn new(runtime_paths: ExecServerRuntimePaths) -> Self {
        Self {
            file_system: LocalFileSystem::with_runtime_paths(runtime_paths),
            file_handles: FileHandleManager::default(),
            notifications: None,
        }
    }

    pub(crate) fn with_notifications(mut self, notifications: RpcNotificationSender) -> Self {
        self.notifications = Some(notifications);
        self
    }

    pub(crate) async fn shutdown(&self) {
        self.file_handles.close_all();
    }

    pub(crate) async fn open(
        &self,
        params: FsOpenParams,
    ) -> Result<FsOpenResponse, JSONRPCErrorError> {
        validate_file_handle_id(&params.handle_id)?;
        // 对位 codex d25c114d49+c39bfa4c8f：mode 透传到开门执行体，句柄表先占槽
        // 再 await 打开（在飞行打开计入 128 槽上限，重复 ID/容量超限在碰文件前拒绝）
        let handle_id = self
            .file_handles
            .open(
                params.handle_id,
                self.file_system
                    .open_file(&params.path, params.mode.into(), params.sandbox.as_ref()),
            )
            .await
            .map_err(map_fs_error)?;
        Ok(FsOpenResponse { handle_id })
    }

    pub(crate) async fn read_block(
        &self,
        params: FsReadBlockParams,
    ) -> Result<FsReadBlockResponse, JSONRPCErrorError> {
        validate_file_handle_id(&params.handle_id)?;
        let block = self
            .file_handles
            .read_block(&params.handle_id, params.offset, params.len)
            .await
            .map_err(map_fs_error)?;
        Ok(FsReadBlockResponse {
            chunk: block.bytes.into(),
            eof: block.eof,
        })
    }

    /// fs/writeBlock（对位 codex c39bfa4c8f）：显式 offset 定位写，委托句柄表
    /// 做块长/区间校验与 spawn_blocking 写循环。
    pub(crate) async fn write_block(
        &self,
        params: FsWriteBlockParams,
    ) -> Result<FsWriteBlockResponse, JSONRPCErrorError> {
        validate_file_handle_id(&params.handle_id)?;
        self.file_handles
            .write_block(&params.handle_id, params.offset, params.chunk.into_inner())
            .await
            .map_err(map_fs_error)?;
        Ok(FsWriteBlockResponse {})
    }

    pub(crate) async fn close(
        &self,
        params: FsCloseParams,
    ) -> Result<FsCloseResponse, JSONRPCErrorError> {
        validate_file_handle_id(&params.handle_id)?;
        // v1.12 语义翻转：写流句柄的 close 即中止，但不再删半成品——文件留在
        // 盘上（对齐 writeBlock/scp 等一切上传工具，为断点续传让路）
        self.file_handles.close(&params.handle_id);
        Ok(FsCloseResponse {})
    }

    pub(crate) async fn read_stream(
        &self,
        params: FsReadStreamParams,
    ) -> Result<FsReadStreamResponse, JSONRPCErrorError> {
        validate_file_handle_id(&params.handle_id)?;
        let block_size = params
            .block_size
            .unwrap_or(DEFAULT_READ_STREAM_BLOCK_SIZE)
            .clamp(1, MAX_READ_STREAM_BLOCK_SIZE);

        // 平台沙箱上下文对调用方透明：get_metadata 走一次性沙箱 helper，
        // open_file 经沙箱开门把 fd/handle 传回本进程（见
        // sandboxed_file_open）——两条路径随后共用同一句柄与流式读循环，
        // 线上 readStream/chunk/done 通知形状不变
        let metadata = self
            .file_system
            .get_metadata(
                &params.path,
                GetMetadataOptions::default(),
                params.sandbox.as_ref(),
            )
            .await
            .map_err(map_fs_error)?;
        let total_size = if metadata.is_file {
            Some(metadata.size)
        } else {
            None
        };

        // 读流恒以 Read 模式开门（句柄表先占槽再 await 打开，与 fs/open 同）
        let handle_id = self
            .file_handles
            .open(
                params.handle_id.clone(),
                self.file_system
                    .open_file(&params.path, OpenMode::Read, params.sandbox.as_ref()),
            )
            .await
            .map_err(map_fs_error)?;

        // 启动后台流式读取任务
        let notifications = self.notifications.clone();
        let file_handles = self.file_handles.clone();
        let handle_id_clone = handle_id.clone();
        let offset = params.offset;
        let len = params.len;
        tokio::spawn(async move {
            let emit_notifications = notifications.clone();
            let emit_handle_id = handle_id_clone.clone();
            let (total_bytes, error) = stream_file_blocks(
                &file_handles,
                &handle_id_clone,
                offset,
                len,
                block_size,
                async move |seq, bytes, eof| {
                    if let Some(notifications) = &emit_notifications {
                        let _ = notifications
                            .notify(
                                crate::protocol::FS_READ_STREAM_CHUNK_METHOD,
                                &FsReadStreamChunkNotification {
                                    handle_id: emit_handle_id.clone(),
                                    seq,
                                    chunk: bytes.into(),
                                    eof,
                                },
                            )
                            .await;
                    }
                    // 通知发送失败（连接已断开）不阻断读取，与既有行为一致；
                    // 连接关闭时 close_all 会让后续 read_block 失败收尾
                    true
                },
            )
            .await;

            if let Some(notifications) = &notifications {
                let _ = notifications
                    .notify(
                        crate::protocol::FS_READ_STREAM_DONE_METHOD,
                        &FsReadStreamDoneNotification {
                            handle_id: handle_id_clone.clone(),
                            total_bytes,
                            error,
                        },
                    )
                    .await;
            }

            file_handles.close(&handle_id_clone);
        });

        Ok(FsReadStreamResponse {
            handle_id,
            total_size,
        })
    }

    pub(crate) async fn read_file(
        &self,
        params: FsReadFileParams,
    ) -> Result<FsReadFileResponse, JSONRPCErrorError> {
        let bytes = self
            .file_system
            .read_file(
                &params.path,
                ReadFileOptions {
                    follow_symlinks: params.follow_symlinks.unwrap_or(true),
                },
                params.sandbox.as_ref(),
            )
            .await
            .map_err(map_fs_error)?;
        Ok(FsReadFileResponse {
            data_base64: STANDARD.encode(bytes),
        })
    }

    pub(crate) async fn write_file(
        &self,
        params: FsWriteFileParams,
    ) -> Result<FsWriteFileResponse, JSONRPCErrorError> {
        let bytes = STANDARD.decode(params.data_base64).map_err(|err| {
            invalid_request(format!(
                "{FS_WRITE_FILE_METHOD} requires valid base64 dataBase64: {err}"
            ))
        })?;
        self.file_system
            .write_file(
                &params.path,
                bytes,
                WriteFileOptions {
                    follow_symlinks: params.follow_symlinks.unwrap_or(true),
                },
                params.sandbox.as_ref(),
            )
            .await
            .map_err(map_fs_error)?;
        Ok(FsWriteFileResponse {})
    }

    /// `fs/writeStream`：打开目标文件并注册写流句柄（v1.12 起收编句柄族——
    /// 与 fs/open 同一条开门链路：写权限档分流、沙箱开门 fd 传递、非沙箱直开，
    /// 随后客户端经 `fs/writeStream/chunk` 通知分片推数据）。
    ///
    /// `offset`（nova 自有续传）：Some(n) = 不截断、从 n 续写（Resume 开门）；
    /// None = 创建/截断。边界：n 不得超当前文件长度（不许隔洞写）；文件不存在
    /// 时 n>0 同样落此拒绝（Resume 开门创建的空文件长度为 0）。
    pub(crate) async fn write_stream(
        &self,
        params: FsWriteStreamParams,
    ) -> Result<FsWriteStreamResponse, JSONRPCErrorError> {
        validate_file_write_handle_id(&params.handle_id)?;

        let offset = params.offset;
        let file_system = self.file_system.clone();
        let path = params.path.clone();
        let sandbox = params.sandbox;
        // 句柄管理器先查重复 ID/占槽再 await 开门（开门即截断/创建，顺序不能反）。
        let handle_id = self
            .file_handles
            .open_write_stream(
                params.handle_id,
                async move {
                    let file = file_system
                        .open_file(
                            &path,
                            match offset {
                                Some(_) => OpenMode::Resume,
                                None => OpenMode::Replace,
                            },
                            sandbox.as_ref(),
                        )
                        .await?;
                    if let Some(offset) = offset {
                        let file_len = file.metadata().await?.len();
                        if offset > file_len {
                            return Err(io::Error::new(
                                io::ErrorKind::InvalidInput,
                                format!(
                                    "file write stream offset {offset} exceeds the current file length {file_len}"
                                ),
                            ));
                        }
                    }
                    Ok(file)
                },
                offset,
            )
            .await
            .map_err(map_fs_error)?;
        Ok(FsWriteStreamResponse { handle_id })
    }

    /// `fs/writeStream/chunk` 是通知（无回执）：业务错误只在句柄状态机内
    /// 流转，由随后的 done 请求回报；这里恒成功，避免协议错误关闭连接。
    pub(crate) async fn write_stream_chunk(
        &self,
        params: FsWriteStreamChunkNotification,
    ) -> Result<(), String> {
        self.file_handles
            .write_stream_chunk(
                &params.handle_id,
                params.seq,
                params.chunk.into_inner(),
                params.eof,
            )
            .await;
        Ok(())
    }

    /// `fs/writeStream/done`：客户端发 eof 收尾，服务端确认成功/失败。
    pub(crate) async fn write_stream_done(
        &self,
        params: FsWriteStreamDoneParams,
    ) -> Result<FsWriteStreamDoneResponse, JSONRPCErrorError> {
        validate_file_write_handle_id(&params.handle_id)?;
        let total_bytes = self
            .file_handles
            .finish_write_stream(&params.handle_id)
            .await
            .map_err(map_fs_error)?;
        Ok(FsWriteStreamDoneResponse {
            handle_id: params.handle_id,
            total_bytes,
        })
    }

    pub(crate) async fn create_directory(
        &self,
        params: FsCreateDirectoryParams,
    ) -> Result<FsCreateDirectoryResponse, JSONRPCErrorError> {
        let recursive = params.recursive.unwrap_or(true);
        self.file_system
            .create_directory(
                &params.path,
                CreateDirectoryOptions {
                    recursive,
                    follow_symlinks: params.follow_symlinks.unwrap_or(true),
                },
                params.sandbox.as_ref(),
            )
            .await
            .map_err(map_fs_error)?;
        Ok(FsCreateDirectoryResponse {})
    }

    pub(crate) async fn get_metadata(
        &self,
        params: FsGetMetadataParams,
    ) -> Result<FsGetMetadataResponse, JSONRPCErrorError> {
        let metadata = self
            .file_system
            .get_metadata(
                &params.path,
                GetMetadataOptions {
                    follow_symlinks: params.follow_symlinks.unwrap_or(true),
                },
                params.sandbox.as_ref(),
            )
            .await
            .map_err(map_fs_error)?;
        Ok(FsGetMetadataResponse {
            is_directory: metadata.is_directory,
            is_file: metadata.is_file,
            is_symlink: metadata.is_symlink,
            size: metadata.size,
            created_at_ms: metadata.created_at_ms,
            modified_at_ms: metadata.modified_at_ms,
        })
    }

    pub(crate) async fn canonicalize(
        &self,
        params: FsCanonicalizeParams,
    ) -> Result<FsCanonicalizeResponse, JSONRPCErrorError> {
        let path = self
            .file_system
            .canonicalize(&params.path, params.sandbox.as_ref())
            .await
            .map_err(map_fs_error)?;
        Ok(FsCanonicalizeResponse { path })
    }

    pub(crate) async fn read_directory(
        &self,
        params: FsReadDirectoryParams,
    ) -> Result<FsReadDirectoryResponse, JSONRPCErrorError> {
        let entries = self
            .file_system
            .read_directory(&params.path, params.sandbox.as_ref())
            .await
            .map_err(map_fs_error)?;
        let entry_count = entries.len();
        if entry_count > MAX_READ_DIRECTORY_ENTRIES {
            return Err(internal_error(format!(
                "{FS_READ_DIRECTORY_METHOD} returned {entry_count} entries; limit is {MAX_READ_DIRECTORY_ENTRIES}"
            )));
        }
        let entries = entries
            .into_iter()
            .map(|entry| FsReadDirectoryEntry {
                file_name: entry.file_name,
                is_directory: entry.is_directory,
                is_file: entry.is_file,
            })
            .collect();
        Ok(FsReadDirectoryResponse { entries })
    }

    pub(crate) async fn walk(
        &self,
        params: FsWalkParams,
    ) -> Result<FsWalkResponse, JSONRPCErrorError> {
        self.file_system
            .walk(&params.path, params.options, params.sandbox.as_ref())
            .await
            .map_err(map_fs_error)
    }

    pub(crate) async fn remove(
        &self,
        params: FsRemoveParams,
    ) -> Result<FsRemoveResponse, JSONRPCErrorError> {
        let recursive = params.recursive.unwrap_or(true);
        let force = params.force.unwrap_or(true);
        self.file_system
            .remove(
                &params.path,
                RemoveOptions {
                    recursive,
                    force,
                    follow_symlinks: params.follow_symlinks.unwrap_or(true),
                },
                params.sandbox.as_ref(),
            )
            .await
            .map_err(map_fs_error)?;
        Ok(FsRemoveResponse {})
    }

    pub(crate) async fn copy(
        &self,
        params: FsCopyParams,
    ) -> Result<FsCopyResponse, JSONRPCErrorError> {
        self.file_system
            .copy(
                &params.source_path,
                &params.destination_path,
                CopyOptions {
                    recursive: params.recursive,
                },
                params.sandbox.as_ref(),
            )
            .await
            .map_err(map_fs_error)?;
        Ok(FsCopyResponse {})
    }
}

fn validate_file_handle_id(handle_id: &str) -> Result<(), JSONRPCErrorError> {
    if handle_id.len() > MAX_FILE_HANDLE_ID_BYTES {
        return Err(invalid_request(format!(
            "file handle ID must not exceed {MAX_FILE_HANDLE_ID_BYTES} bytes"
        )));
    }
    Ok(())
}

fn validate_file_write_handle_id(handle_id: &str) -> Result<(), JSONRPCErrorError> {
    if handle_id.len() > MAX_FILE_WRITE_HANDLE_ID_BYTES {
        return Err(invalid_request(format!(
            "file write handle ID must not exceed {MAX_FILE_WRITE_HANDLE_ID_BYTES} bytes"
        )));
    }
    Ok(())
}

fn map_fs_error(err: io::Error) -> JSONRPCErrorError {
    match err.kind() {
        io::ErrorKind::NotFound => not_found(err.to_string()),
        io::ErrorKind::InvalidInput | io::ErrorKind::PermissionDenied => {
            invalid_request(err.to_string())
        }
        _ => internal_error(err.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use nova_exec_server_protocol_core::protocol::NetworkAccess;
    use nova_exec_server_protocol_core::protocol::SandboxPolicy;
    use nova_exec_server_utils_path_uri::PathUri;
    use pretty_assertions::assert_eq;

    use super::*;
    use crate::ByteChunk;
    use crate::FileSystemSandboxContext;
    use crate::protocol::FsReadFileParams;
    use crate::protocol::FsWriteFileParams;

    fn test_runtime_paths() -> ExecServerRuntimePaths {
        ExecServerRuntimePaths::new(
            std::env::current_exe().expect("current exe"),
            /*nova_linux_sandbox_exe*/ None,
        )
        .expect("runtime paths")
    }

    /// 等写任务把已入 channel 的块落定（镜像结构下 close/断开只摘条目——
    /// 写任务先排空队列再退出，内容为最终一致；此处按超时轮询等待）。
    async fn await_file_content(path: &std::path::Path, expected: &[u8]) {
        tokio::time::timeout(std::time::Duration::from_secs(5), async {
            loop {
                if std::fs::read(path).is_ok_and(|content| content == expected) {
                    return;
                }
                tokio::time::sleep(std::time::Duration::from_millis(10)).await;
            }
        })
        .await
        .unwrap_or_else(|_| {
            panic!(
                "timed out waiting for file content {:?} (actual: {:?})",
                expected,
                std::fs::read(path).ok()
            )
        });
    }

    /// 等写任务在 done 交账后摘句柄退出（respond 先达、摘除在其后一拍——
    /// 有界轮询，不赌调度窗口）。
    async fn wait_for_handle_removal(handler: &FileSystemHandler) {
        tokio::time::timeout(std::time::Duration::from_secs(5), async {
            while handler.file_handles.open_handle_count() > 0 {
                tokio::time::sleep(std::time::Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("write stream handle should be removed");
    }

    #[tokio::test]
    async fn write_stream_aggregates_chunks_and_done_confirms_total_bytes() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let handler = FileSystemHandler::new(test_runtime_paths());
        let path =
            PathUri::from_host_native_path(temp_dir.path().join("out.bin")).expect("path URI");

        let opened = handler
            .write_stream(FsWriteStreamParams {
                handle_id: "w1".to_string(),
                path: path.clone(),
                offset: None,
                sandbox: None,
            })
            .await
            .expect("open write stream");
        assert_eq!(opened.handle_id, "w1");

        for (seq, bytes, eof) in [
            (0, b"hello ".to_vec(), false),
            (1, b"wor".to_vec(), false),
            (2, b"ld".to_vec(), true),
        ] {
            handler
                .write_stream_chunk(FsWriteStreamChunkNotification {
                    handle_id: "w1".to_string(),
                    seq,
                    chunk: ByteChunk::from(bytes),
                    eof,
                })
                .await
                .expect("chunk");
        }

        let done = handler
            .write_stream_done(FsWriteStreamDoneParams {
                handle_id: "w1".to_string(),
            })
            .await
            .expect("done");
        assert_eq!(done.total_bytes, 11);

        let response = handler
            .read_file(FsReadFileParams {
                path,
                follow_symlinks: None,
                sandbox: None,
            })
            .await
            .expect("read file");
        assert_eq!(response.data_base64, STANDARD.encode("hello world"));
    }

    #[tokio::test]
    async fn write_stream_close_aborts_and_keeps_partial_file() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let handler = FileSystemHandler::new(test_runtime_paths());
        let native_path = temp_dir.path().join("aborted.bin");
        let path = PathUri::from_host_native_path(&native_path).expect("path URI");

        handler
            .write_stream(FsWriteStreamParams {
                handle_id: "w1".to_string(),
                path,
                offset: None,
                sandbox: None,
            })
            .await
            .expect("open write stream");
        handler
            .write_stream_chunk(FsWriteStreamChunkNotification {
                handle_id: "w1".to_string(),
                seq: 0,
                chunk: ByteChunk::from(b"half".to_vec()),
                eof: false,
            })
            .await
            .expect("chunk");

        // fs/close 对写流句柄即中止（v1.12 语义翻转：不删半成品——文件留在
        // 盘上，为断点续传让路）
        handler
            .close(FsCloseParams {
                handle_id: "w1".to_string(),
            })
            .await
            .expect("close");
        await_file_content(&native_path, b"half").await;

        let err = handler
            .write_stream_done(FsWriteStreamDoneParams {
                handle_id: "w1".to_string(),
            })
            .await
            .expect_err("done after abort should fail");
        assert_eq!(err.code, -32004);
    }

    #[tokio::test]
    async fn write_stream_done_without_eof_fails_and_keeps_partial_file() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let handler = FileSystemHandler::new(test_runtime_paths());
        let native_path = temp_dir.path().join("no-eof.bin");
        let path = PathUri::from_host_native_path(&native_path).expect("path URI");

        handler
            .write_stream(FsWriteStreamParams {
                handle_id: "w1".to_string(),
                path,
                offset: None,
                sandbox: None,
            })
            .await
            .expect("open write stream");
        handler
            .write_stream_chunk(FsWriteStreamChunkNotification {
                handle_id: "w1".to_string(),
                seq: 0,
                chunk: ByteChunk::from(b"half".to_vec()),
                eof: false,
            })
            .await
            .expect("chunk");

        let err = handler
            .write_stream_done(FsWriteStreamDoneParams {
                handle_id: "w1".to_string(),
            })
            .await
            .expect_err("done without eof should fail");
        assert_eq!(err.code, -32600);
        // 语义翻转：失败也不删半成品
        assert_eq!(
            std::fs::read(&native_path).expect("partial file stays"),
            b"half"
        );
    }

    #[tokio::test]
    async fn write_stream_out_of_order_chunk_fails_done_and_keeps_partial_file() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let handler = FileSystemHandler::new(test_runtime_paths());
        let native_path = temp_dir.path().join("out-of-order.bin");
        let path = PathUri::from_host_native_path(&native_path).expect("path URI");

        handler
            .write_stream(FsWriteStreamParams {
                handle_id: "w1".to_string(),
                path,
                offset: None,
                sandbox: None,
            })
            .await
            .expect("open write stream");
        for (seq, eof) in [(0, false), (2, true)] {
            handler
                .write_stream_chunk(FsWriteStreamChunkNotification {
                    handle_id: "w1".to_string(),
                    seq,
                    chunk: ByteChunk::from(b"aa".to_vec()),
                    eof,
                })
                .await
                .expect("chunk");
        }

        let err = handler
            .write_stream_done(FsWriteStreamDoneParams {
                handle_id: "w1".to_string(),
            })
            .await
            .expect_err("done should report the seq violation");
        assert_eq!(err.code, -32600);
        assert!(err.message.contains("expected seq 1, got 2"));
        assert_eq!(
            std::fs::read(&native_path).expect("partial file stays"),
            b"aa"
        );
    }

    #[tokio::test]
    async fn write_stream_shutdown_keeps_partial_files() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let handler = FileSystemHandler::new(test_runtime_paths());
        let native_path = temp_dir.path().join("shutdown.bin");
        let path = PathUri::from_host_native_path(&native_path).expect("path URI");

        handler
            .write_stream(FsWriteStreamParams {
                handle_id: "w1".to_string(),
                path,
                offset: None,
                sandbox: None,
            })
            .await
            .expect("open write stream");
        handler
            .write_stream_chunk(FsWriteStreamChunkNotification {
                handle_id: "w1".to_string(),
                seq: 0,
                chunk: ByteChunk::from(b"half".to_vec()),
                eof: false,
            })
            .await
            .expect("chunk");

        handler.shutdown().await;
        // 语义翻转：连接关闭也不再删半成品（写任务排空队列后退出）
        await_file_content(&native_path, b"half").await;
    }

    /// eof 后再来块：违规记为失败终态（file_handle.rs 的 eof_seen 校验臂），
    /// done 回报 -32600 且文案含 "received chunk after eof"；违规块不落盘，
    /// 半成品保留。
    #[tokio::test]
    async fn write_stream_done_reports_chunk_after_eof_and_keeps_partial_file() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let handler = FileSystemHandler::new(test_runtime_paths());
        let native_path = temp_dir.path().join("after-eof.bin");
        let path = PathUri::from_host_native_path(&native_path).expect("path URI");

        handler
            .write_stream(FsWriteStreamParams {
                handle_id: "w1".to_string(),
                path,
                offset: None,
                sandbox: None,
            })
            .await
            .expect("open write stream");
        for (seq, bytes, eof) in [(0, b"ab".to_vec(), true), (1, b"cd".to_vec(), false)] {
            handler
                .write_stream_chunk(FsWriteStreamChunkNotification {
                    handle_id: "w1".to_string(),
                    seq,
                    chunk: ByteChunk::from(bytes),
                    eof,
                })
                .await
                .expect("chunk");
        }

        let err = handler
            .write_stream_done(FsWriteStreamDoneParams {
                handle_id: "w1".to_string(),
            })
            .await
            .expect_err("done should report the chunk after eof");
        assert_eq!(err.code, -32600);
        assert!(
            err.message.contains("received chunk after eof"),
            "unexpected error message: {}",
            err.message,
        );
        // 违规块不落盘：半成品只含 eof 前的内容
        assert_eq!(
            std::fs::read(&native_path).expect("partial file stays"),
            b"ab"
        );
    }

    /// done 重复：首次交账后写任务摘句柄退出，二次 done 落 unknown handle
    /// （-32004）。
    #[tokio::test]
    async fn write_stream_done_twice_reports_unknown_handle() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let handler = FileSystemHandler::new(test_runtime_paths());
        let native_path = temp_dir.path().join("done-twice.bin");
        let path = PathUri::from_host_native_path(&native_path).expect("path URI");

        handler
            .write_stream(FsWriteStreamParams {
                handle_id: "w1".to_string(),
                path,
                offset: None,
                sandbox: None,
            })
            .await
            .expect("open write stream");
        handler
            .write_stream_chunk(FsWriteStreamChunkNotification {
                handle_id: "w1".to_string(),
                seq: 0,
                chunk: ByteChunk::from(b"ab".to_vec()),
                eof: true,
            })
            .await
            .expect("chunk");

        let done = handler
            .write_stream_done(FsWriteStreamDoneParams {
                handle_id: "w1".to_string(),
            })
            .await
            .expect("done");
        assert_eq!(done.total_bytes, 2);
        // 交账后写任务随即摘句柄退出（respond 先达、摘除在其后一拍）
        wait_for_handle_removal(&handler).await;

        let err = handler
            .write_stream_done(FsWriteStreamDoneParams {
                handle_id: "w1".to_string(),
            })
            .await
            .expect_err("repeated done should report unknown handle");
        assert_eq!(err.code, -32004);
    }

    /// close_all（shutdown）后 done：句柄表已清空，done 落 unknown handle
    /// （-32004）；半成品保留。
    #[tokio::test]
    async fn write_stream_done_after_shutdown_reports_unknown_handle() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let handler = FileSystemHandler::new(test_runtime_paths());
        let native_path = temp_dir.path().join("shutdown-done.bin");
        let path = PathUri::from_host_native_path(&native_path).expect("path URI");

        handler
            .write_stream(FsWriteStreamParams {
                handle_id: "w1".to_string(),
                path,
                offset: None,
                sandbox: None,
            })
            .await
            .expect("open write stream");
        handler
            .write_stream_chunk(FsWriteStreamChunkNotification {
                handle_id: "w1".to_string(),
                seq: 0,
                chunk: ByteChunk::from(b"half".to_vec()),
                eof: false,
            })
            .await
            .expect("chunk");

        handler.shutdown().await;
        let err = handler
            .write_stream_done(FsWriteStreamDoneParams {
                handle_id: "w1".to_string(),
            })
            .await
            .expect_err("done after shutdown should report unknown handle");
        assert_eq!(err.code, -32004);
        // 镜像结构下 close_all 只摘条目——写任务排空队列后退出，半成品保留
        await_file_content(&native_path, b"half").await;
    }

    /// 两写流并发同 path（不同 handle_id）：协议不加锁不互斥——开门即截断
    /// （offset=None 走 Replace，后开者把先开者已落盘的内容截为空）；两流各自
    /// 持独立句柄与游标经定位写落盘（无共享文件偏移），重叠区间后写者胜；
    /// 两边 done 各自成功，totalBytes 为各自游标终点（全量语义）。
    /// 依据：file_handle.rs `open_entry`（每流独立 File）与
    /// `run_write_stream_task`（游标住进各自任务栈、write_block_at 定位写）。
    #[tokio::test]
    async fn write_stream_concurrent_streams_on_one_path_keep_independent_cursors() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let handler = FileSystemHandler::new(test_runtime_paths());
        let native_path = temp_dir.path().join("shared.bin");
        std::fs::write(&native_path, b"original").expect("write fixture");
        let path = PathUri::from_host_native_path(&native_path).expect("path URI");
        let open_stream = |handle_id: &str| {
            handler.write_stream(FsWriteStreamParams {
                handle_id: handle_id.to_string(),
                path: path.clone(),
                offset: None,
                sandbox: None,
            })
        };
        let push_chunk = |handle_id: &str, seq: u64, bytes: &'static [u8], eof: bool| {
            handler.write_stream_chunk(FsWriteStreamChunkNotification {
                handle_id: handle_id.to_string(),
                seq,
                chunk: ByteChunk::from(bytes.to_vec()),
                eof,
            })
        };

        // 开门即截断：第一条流的 open 返回时文件已清空
        open_stream("w1").await.expect("open first stream");
        assert_eq!(std::fs::read(&native_path).expect("read file"), b"");
        push_chunk("w1", 0, b"hello ", false).await.expect("chunk");
        await_file_content(&native_path, b"hello ").await;

        // 后开者截断：第二条流 open（Replace）把先开者已落盘的内容截为空
        open_stream("w2").await.expect("open second stream");
        assert_eq!(std::fs::read(&native_path).expect("read file"), b"");
        push_chunk("w2", 0, b"WORLD!", false).await.expect("chunk");
        await_file_content(&native_path, b"WORLD!").await;

        // 游标独立：w1 的游标仍是 6（截断不回卷其任务栈游标），重叠区间
        // [0,6) 后写者（w2）胜；w1 续写落在 w2 内容之后
        push_chunk("w1", 1, b"ONE", true).await.expect("chunk");
        let done = handler
            .write_stream_done(FsWriteStreamDoneParams {
                handle_id: "w1".to_string(),
            })
            .await
            .expect("done first stream");
        assert_eq!(done.total_bytes, 9);
        assert_eq!(
            std::fs::read(&native_path).expect("read file"),
            b"WORLD!ONE"
        );

        // w2 续写同样落在自身游标 6 处（覆写 [6,10)），两边 done 各自成功
        push_chunk("w2", 1, b"-TWO", true).await.expect("chunk");
        let done = handler
            .write_stream_done(FsWriteStreamDoneParams {
                handle_id: "w2".to_string(),
            })
            .await
            .expect("done second stream");
        assert_eq!(done.total_bytes, 10);
        assert_eq!(
            std::fs::read(&native_path).expect("read file"),
            b"WORLD!-TWO"
        );
    }

    /// offset 续传（v1.12，nova 自有通道）：中止后文件留在盘上，
    /// 带 offset 重开不截断、从断点续写；done 的 totalBytes 为全量语义
    /// （offset 起点 + 本次流式字节数）。
    #[tokio::test]
    async fn write_stream_resumes_from_offset_after_close() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let handler = FileSystemHandler::new(test_runtime_paths());
        let native_path = temp_dir.path().join("resumed.bin");
        let path = PathUri::from_host_native_path(&native_path).expect("path URI");

        handler
            .write_stream(FsWriteStreamParams {
                handle_id: "w1".to_string(),
                path: path.clone(),
                offset: None,
                sandbox: None,
            })
            .await
            .expect("open write stream");
        handler
            .write_stream_chunk(FsWriteStreamChunkNotification {
                handle_id: "w1".to_string(),
                seq: 0,
                chunk: ByteChunk::from(b"hello ".to_vec()),
                eof: false,
            })
            .await
            .expect("chunk");
        // 中止（断连/close）：文件留在盘上（镜像结构下 close 只摘条目——
        // 写任务先排空队列再退出，等有界轮询落定再续传，不赌调度窗口）
        handler
            .close(FsCloseParams {
                handle_id: "w1".to_string(),
            })
            .await
            .expect("close");
        await_file_content(&native_path, b"hello ").await;

        // 续传：从 6 续写，不截断
        handler
            .write_stream(FsWriteStreamParams {
                handle_id: "w2".to_string(),
                path: path.clone(),
                offset: Some(6),
                sandbox: None,
            })
            .await
            .expect("reopen write stream");
        handler
            .write_stream_chunk(FsWriteStreamChunkNotification {
                handle_id: "w2".to_string(),
                seq: 0,
                chunk: ByteChunk::from(b"world".to_vec()),
                eof: true,
            })
            .await
            .expect("chunk");
        let done = handler
            .write_stream_done(FsWriteStreamDoneParams {
                handle_id: "w2".to_string(),
            })
            .await
            .expect("done");
        assert_eq!(done.total_bytes, 11);
        assert_eq!(
            std::fs::read(&native_path).expect("read file"),
            b"hello world"
        );
    }

    /// offset 越界（超当前文件长度）即拒——不许隔洞写；文件不存在 +
    /// Some(offset>0) 同此拒绝。
    #[tokio::test]
    async fn write_stream_rejects_offset_beyond_file_end() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let handler = FileSystemHandler::new(test_runtime_paths());
        let native_path = temp_dir.path().join("bounded.bin");
        std::fs::write(&native_path, b"abc").expect("write fixture");
        let path = PathUri::from_host_native_path(&native_path).expect("path URI");

        let err = handler
            .write_stream(FsWriteStreamParams {
                handle_id: "w1".to_string(),
                path: path.clone(),
                offset: Some(4),
                sandbox: None,
            })
            .await
            .expect_err("offset beyond file length should fail");
        assert_eq!(err.code, -32600);
        // 拒绝不改动既有内容
        assert_eq!(std::fs::read(&native_path).expect("read file"), b"abc");
        assert_eq!(handler.file_handles.open_handle_count(), 0);

        // 文件不存在 + offset>0：同样 invalid_params
        let missing = PathUri::from_host_native_path(temp_dir.path().join("missing.bin"))
            .expect("path URI");
        let err = handler
            .write_stream(FsWriteStreamParams {
                handle_id: "w2".to_string(),
                path: missing,
                offset: Some(1),
                sandbox: None,
            })
            .await
            .expect_err("resume beyond a missing file should fail");
        assert_eq!(err.code, -32600);
        assert_eq!(handler.file_handles.open_handle_count(), 0);
    }

    /// offset Some(0)：不截断但从 0 覆写——与创建/截断的默认语义区分。
    #[tokio::test]
    async fn write_stream_offset_zero_overwrites_without_truncating() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let handler = FileSystemHandler::new(test_runtime_paths());
        let native_path = temp_dir.path().join("overwrite.bin");
        std::fs::write(&native_path, b"aabbcc").expect("write fixture");
        let path = PathUri::from_host_native_path(&native_path).expect("path URI");

        handler
            .write_stream(FsWriteStreamParams {
                handle_id: "w1".to_string(),
                path: path.clone(),
                offset: Some(0),
                sandbox: None,
            })
            .await
            .expect("open write stream");
        handler
            .write_stream_chunk(FsWriteStreamChunkNotification {
                handle_id: "w1".to_string(),
                seq: 0,
                chunk: ByteChunk::from(b"zz".to_vec()),
                eof: true,
            })
            .await
            .expect("chunk");
        let done = handler
            .write_stream_done(FsWriteStreamDoneParams {
                handle_id: "w1".to_string(),
            })
            .await
            .expect("done");
        assert_eq!(done.total_bytes, 2);
        // 不截断：覆写区之外的尾部内容保留
        assert_eq!(std::fs::read(&native_path).expect("read file"), b"zzbbcc");
    }

    #[tokio::test]
    async fn no_platform_sandbox_policies_do_not_require_configured_sandbox_helper() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let runtime_paths = ExecServerRuntimePaths::new(
            std::env::current_exe().expect("current exe"),
            /*nova_linux_sandbox_exe*/ None,
        )
        .expect("runtime paths");
        let handler = FileSystemHandler::new(runtime_paths);
        let sandbox_cwd = PathUri::from_host_native_path(temp_dir.path()).expect("tempdir URI");
        let sandbox_context = |sandbox_policy| {
            FileSystemSandboxContext::from_legacy_sandbox_policy(
                sandbox_policy,
                sandbox_cwd.clone(),
            )
            .expect("sandbox context")
        };

        for (file_name, sandbox_policy) in [
            ("danger.txt", SandboxPolicy::DangerFullAccess),
            (
                "external.txt",
                SandboxPolicy::ExternalSandbox {
                    network_access: NetworkAccess::Restricted,
                },
            ),
        ] {
            let path =
                PathUri::from_host_native_path(temp_dir.path().join(file_name)).expect("path URI");

            handler
                .write_file(FsWriteFileParams {
                    path: path.clone(),
                    follow_symlinks: None,
                    data_base64: STANDARD.encode("ok"),
                    sandbox: Some(sandbox_context(sandbox_policy.clone())),
                })
                .await
                .expect("write file");

            let canonicalized = handler
                .canonicalize(FsCanonicalizeParams {
                    path: path.clone(),
                    sandbox: Some(sandbox_context(sandbox_policy.clone())),
                })
                .await
                .expect("canonicalize file");
            assert_eq!(
                canonicalized.path,
                PathUri::from_host_native_path(
                    std::fs::canonicalize(temp_dir.path().join(file_name)).expect("canonical path"),
                )
                .expect("canonical path URI"),
            );

            let response = handler
                .read_file(FsReadFileParams {
                    path,
                    follow_symlinks: None,
                    sandbox: Some(sandbox_context(sandbox_policy)),
                })
                .await
                .expect("read file");

            assert_eq!(response.data_base64, STANDARD.encode("ok"));
        }
    }

    /// 带平台沙箱上下文的 fs/readStream 端到端测试：真实拉起沙箱化
    /// fs_helper 子进程开门并传回 fd（helper 即当前测试二进制，见下方 ctor 分派），
    /// executor 自持句柄推流——线上 chunk/done 通知形状与非沙箱路径一致。
    #[cfg(target_os = "macos")]
    mod sandboxed_read_stream {
        use std::path::Path;
        use std::time::Duration;

        use nova_exec_server_protocol_core::models::PermissionProfile;
        use nova_exec_server_protocol_core::permissions::FileSystemAccessMode;
        use nova_exec_server_protocol_core::permissions::FileSystemPath;
        use nova_exec_server_protocol_core::permissions::FileSystemSandboxEntry;
        use nova_exec_server_protocol_core::permissions::FileSystemSandboxPolicy;
        use nova_exec_server_protocol_core::permissions::NetworkSandboxPolicy;
        use nova_exec_server_utils_absolute_path::AbsolutePathBuf;
        use pretty_assertions::assert_eq;
        use tokio::sync::mpsc;
        use tokio::time::timeout;

        use super::*;
        use crate::rpc::RpcServerOutboundMessage;

        /// `cargo test` 二进制同时充当沙箱 fs helper 的执行体：
        /// FileSystemSandboxRunner 以 current_exe + `--nova-run-as-fs-helper`
        /// 重启本进程，这里在 libtest 启动前接管进入 helper 主流程（不返回）。
        #[ctor::ctor]
        fn dispatch_embedded_fs_helper() {
            let mut args = std::env::args_os();
            let _program = args.next();
            if args.next().as_deref()
                == Some(std::ffi::OsStr::new(
                    crate::fs_helper::NOVA_EXEC_SERVER_FS_HELPER_ARG1,
                ))
            {
                crate::run_fs_helper_main();
            }
        }

        /// 只允许读 `root` 的平台沙箱上下文（read-only 于工作区根）
        fn read_only_sandbox(root: &Path) -> FileSystemSandboxContext {
            let policy = FileSystemSandboxPolicy::restricted(vec![FileSystemSandboxEntry {
                path: FileSystemPath::Path {
                    path: AbsolutePathBuf::from_absolute_path(root)
                        .expect("absolute root")
                        .into(),
                },
                access: FileSystemAccessMode::Read,
                missing_path_behavior: None,
            }]);
            let sandbox = FileSystemSandboxContext::from_permission_profile(
                PermissionProfile::from_runtime_permissions(
                    &policy,
                    NetworkSandboxPolicy::Restricted,
                ),
                PathUri::from_host_native_path(root).expect("cwd URI"),
            );
            assert!(sandbox.should_read_from_sandbox());
            sandbox
        }

        fn sandboxed_handler() -> (FileSystemHandler, mpsc::Receiver<RpcServerOutboundMessage>) {
            sandboxed_handler_with_capacity(64)
        }

        fn sandboxed_handler_with_capacity(
            capacity: usize,
        ) -> (FileSystemHandler, mpsc::Receiver<RpcServerOutboundMessage>) {
            let (outgoing_tx, outgoing_rx) = mpsc::channel(capacity);
            (
                FileSystemHandler::new(test_runtime_paths())
                    .with_notifications(RpcNotificationSender::new(outgoing_tx)),
                outgoing_rx,
            )
        }

        enum StreamFrame {
            Chunk(FsReadStreamChunkNotification),
            Done(FsReadStreamDoneNotification),
        }

        async fn next_stream_frame(
            rx: &mut mpsc::Receiver<RpcServerOutboundMessage>,
        ) -> StreamFrame {
            loop {
                let message = timeout(Duration::from_secs(30), rx.recv())
                    .await
                    .expect("timed out waiting for stream notification")
                    .expect("notification channel closed");
                let RpcServerOutboundMessage::Notification(notification) = message else {
                    continue;
                };
                let params = notification.params.expect("notification params");
                match notification.method.as_str() {
                    crate::protocol::FS_READ_STREAM_CHUNK_METHOD => {
                        return StreamFrame::Chunk(
                            serde_json::from_value(params).expect("chunk notification"),
                        );
                    }
                    crate::protocol::FS_READ_STREAM_DONE_METHOD => {
                        return StreamFrame::Done(
                            serde_json::from_value(params).expect("done notification"),
                        );
                    }
                    _ => {}
                }
            }
        }

        /// 等待流式读后台任务收尾：done 送达后任务注销句柄退出
        async fn wait_for_handle_close(handler: &FileSystemHandler) {
            timeout(Duration::from_secs(10), async {
                while handler.file_handles.open_handle_count() > 0 {
                    tokio::time::sleep(Duration::from_millis(20)).await;
                }
            })
            .await
            .expect("read stream handle should be closed after the stream finishes");
        }

        #[tokio::test]
        async fn streams_file_via_sandboxed_helper() {
            let temp_dir = tempfile::tempdir().expect("tempdir");
            // macOS tempdir 位于 /var（符号链接），沙箱策略与路径一律用真实路径
            let root = std::fs::canonicalize(temp_dir.path()).expect("canonical root");
            let data: Vec<u8> = (0..600_000u32).map(|i| (i % 251) as u8).collect();
            let file_path = root.join("data.bin");
            std::fs::write(&file_path, &data).expect("write fixture");

            let (handler, mut rx) = sandboxed_handler();
            let response = handler
                .read_stream(FsReadStreamParams {
                    handle_id: "rs-ok".to_string(),
                    path: PathUri::from_host_native_path(&file_path).expect("path URI"),
                    offset: 0,
                    len: None,
                    block_size: Some(64 * 1024),
                    sandbox: Some(read_only_sandbox(&root)),
                })
                .await
                .expect("read stream");
            assert_eq!(response.handle_id, "rs-ok");
            assert_eq!(response.total_size, Some(data.len() as u64));

            let mut chunks = Vec::new();
            let done = loop {
                match next_stream_frame(&mut rx).await {
                    StreamFrame::Chunk(chunk) => chunks.push(chunk),
                    StreamFrame::Done(done) => break done,
                }
            };

            // 线上形状不变：seq 从 0 递增、仅末块 eof、字节拼接还原、done 无错
            assert!(chunks.len() > 1, "expected multiple chunks");
            for (index, chunk) in chunks.iter().enumerate() {
                assert_eq!(chunk.handle_id, "rs-ok");
                assert_eq!(chunk.seq, index as u64);
                assert_eq!(chunk.eof, index + 1 == chunks.len());
            }
            let reassembled: Vec<u8> = chunks
                .iter()
                .flat_map(|chunk| chunk.chunk.clone().into_inner())
                .collect();
            assert_eq!(reassembled, data);
            assert_eq!(done.handle_id, "rs-ok");
            assert_eq!(done.total_bytes, data.len() as u64);
            assert_eq!(done.error, None);

            wait_for_handle_close(&handler).await;
        }

        #[tokio::test]
        async fn rejects_read_outside_allowed_roots() {
            let allowed = tempfile::tempdir().expect("tempdir");
            let allowed_root = std::fs::canonicalize(allowed.path()).expect("canonical root");
            let outside = tempfile::tempdir().expect("tempdir");
            let outside_root = std::fs::canonicalize(outside.path()).expect("canonical root");
            let secret_path = outside_root.join("secret.txt");
            std::fs::write(&secret_path, b"top secret").expect("write fixture");

            let (handler, _rx) = sandboxed_handler();
            let err = handler
                .read_stream(FsReadStreamParams {
                    handle_id: "rs-denied".to_string(),
                    path: PathUri::from_host_native_path(&secret_path).expect("path URI"),
                    offset: 0,
                    len: None,
                    block_size: None,
                    sandbox: Some(read_only_sandbox(&allowed_root)),
                })
                .await
                .expect_err("read outside the sandbox should be rejected");

            // Seatbelt 拒绝 → EPERM → invalid_request（元数据/开门失败同步以
            // RPC 错误返回，与非沙箱路径语义一致）
            assert_eq!(err.code, -32600);
            assert_eq!(handler.file_handles.open_handle_count(), 0);
        }

        #[tokio::test]
        async fn close_interrupts_stream() {
            let temp_dir = tempfile::tempdir().expect("tempdir");
            let root = std::fs::canonicalize(temp_dir.path()).expect("canonical root");
            let data = vec![7u8; 16 * 1024 * 1024];
            let file_path = root.join("big.bin");
            std::fs::write(&file_path, &data).expect("write fixture");

            // 小容量通道制造背压：流式读任务很快堵在通知上，close 必然抢先于读完
            let (handler, mut rx) = sandboxed_handler_with_capacity(2);
            let response = handler
                .read_stream(FsReadStreamParams {
                    handle_id: "rs-int".to_string(),
                    path: PathUri::from_host_native_path(&file_path).expect("path URI"),
                    offset: 0,
                    len: None,
                    block_size: None,
                    sandbox: Some(read_only_sandbox(&root)),
                })
                .await
                .expect("read stream");
            assert_eq!(response.total_size, Some(data.len() as u64));

            // 等首块到达（确认流在飞），随后 fs/close 中断
            let first = next_stream_frame(&mut rx).await;
            assert!(matches!(first, StreamFrame::Chunk(_)));
            handler
                .close(FsCloseParams {
                    handle_id: "rs-int".to_string(),
                })
                .await
                .expect("close");
            // close 同步摘除句柄（fd 传递后无长命 helper，无需另行收尸）
            assert_eq!(handler.file_handles.open_handle_count(), 0);

            // 中断后必到终态 done（带 error），且提前结束（16MB/256KB 共 64 块）
            let mut chunk_count = 1usize;
            let done = loop {
                match next_stream_frame(&mut rx).await {
                    StreamFrame::Chunk(_) => chunk_count += 1,
                    StreamFrame::Done(done) => break done,
                }
            };
            assert_eq!(done.handle_id, "rs-int");
            assert!(
                done.error.is_some(),
                "interrupted stream should finish with an error"
            );
            assert!(
                chunk_count < 64,
                "stream should stop before reading the whole file, got {chunk_count} chunks"
            );
        }
    }

    /// 带平台沙箱上下文的 fs/writeStream 端到端测试：真实拉起沙箱化
    /// fs_helper 子进程开门（helper 即当前测试二进制，ctor 分派与读流模块相同；
    /// v1.12 起写流收编句柄族——helper 只开门传 fd，写落 executor 进程内的
    /// 共享定位写核心）。
    #[cfg(target_os = "macos")]
    mod sandboxed_write_stream {
        use std::path::Path;

        use nova_exec_server_protocol_core::models::PermissionProfile;
        use nova_exec_server_protocol_core::permissions::FileSystemAccessMode;
        use nova_exec_server_protocol_core::permissions::FileSystemPath;
        use nova_exec_server_protocol_core::permissions::FileSystemSandboxEntry;
        use nova_exec_server_protocol_core::permissions::FileSystemSandboxPolicy;
        use nova_exec_server_protocol_core::permissions::NetworkSandboxPolicy;
        use nova_exec_server_utils_absolute_path::AbsolutePathBuf;
        use pretty_assertions::assert_eq;

        use super::*;
        use crate::ByteChunk;

        /// `cargo test` 二进制同时充当沙箱 fs helper 的执行体（与读流模块同一
        /// 分派）：FileSystemSandboxRunner 以 current_exe + `--nova-run-as-fs-helper`
        /// 重启本进程，这里在 libtest 启动前接管进入 helper 主流程（不返回）。
        #[ctor::ctor]
        fn dispatch_embedded_fs_helper_for_write() {
            let mut args = std::env::args_os();
            let _program = args.next();
            if args.next().as_deref()
                == Some(std::ffi::OsStr::new(
                    crate::fs_helper::NOVA_EXEC_SERVER_FS_HELPER_ARG1,
                ))
            {
                crate::run_fs_helper_main();
            }
        }

        /// 允许读写 `root` 的平台沙箱上下文（工作区根可写）
        fn writable_sandbox(root: &Path) -> FileSystemSandboxContext {
            let policy = FileSystemSandboxPolicy::restricted(vec![FileSystemSandboxEntry {
                path: FileSystemPath::Path {
                    path: AbsolutePathBuf::from_absolute_path(root)
                        .expect("absolute root")
                        .into(),
                },
                access: FileSystemAccessMode::Write,
                missing_path_behavior: None,
            }]);
            let sandbox = FileSystemSandboxContext::from_permission_profile(
                PermissionProfile::from_runtime_permissions(
                    &policy,
                    NetworkSandboxPolicy::Restricted,
                ),
                PathUri::from_host_native_path(root).expect("cwd URI"),
            );
            assert!(sandbox.should_write_into_sandbox());
            sandbox
        }

        fn write_stream_params(path: &Path, handle_id: &str, root: &Path) -> FsWriteStreamParams {
            FsWriteStreamParams {
                handle_id: handle_id.to_string(),
                path: PathUri::from_host_native_path(path).expect("path URI"),
                offset: None,
                sandbox: Some(writable_sandbox(root)),
            }
        }

        async fn push_chunk(
            handler: &FileSystemHandler,
            handle_id: &str,
            seq: u64,
            bytes: &[u8],
            eof: bool,
        ) {
            handler
                .write_stream_chunk(FsWriteStreamChunkNotification {
                    handle_id: handle_id.to_string(),
                    seq,
                    chunk: ByteChunk::from(bytes.to_vec()),
                    eof,
                })
                .await
                .expect("chunk");
        }

        #[tokio::test]
        async fn writes_file_via_sandboxed_helper() {
            let temp_dir = tempfile::tempdir().expect("tempdir");
            // macOS tempdir 位于 /var（符号链接），沙箱策略与路径一律用真实路径
            let root = std::fs::canonicalize(temp_dir.path()).expect("canonical root");
            let data: Vec<u8> = (0..600_000u32).map(|i| (i % 251) as u8).collect();
            let file_path = root.join("out.bin");

            let handler = FileSystemHandler::new(test_runtime_paths());
            let opened = handler
                .write_stream(write_stream_params(&file_path, "ws-ok", &root))
                .await
                .expect("open write stream");
            assert_eq!(opened.handle_id, "ws-ok");
            // 开门成功 = 一次性 helper 已在沙箱内创建/截断目标文件并传回句柄
            assert!(file_path.exists());

            // 64KB 分块推送（末块带 eof），executor 内经共享定位写核心落盘
            let block = 64 * 1024;
            let blocks: Vec<&[u8]> = data.chunks(block).collect();
            assert!(blocks.len() > 1, "expected multiple chunks");
            for (index, bytes) in blocks.iter().enumerate() {
                push_chunk(
                    &handler,
                    "ws-ok",
                    index as u64,
                    bytes,
                    index + 1 == blocks.len(),
                )
                .await;
            }

            let done = handler
                .write_stream_done(FsWriteStreamDoneParams {
                    handle_id: "ws-ok".to_string(),
                })
                .await
                .expect("done");
            assert_eq!(done.handle_id, "ws-ok");
            assert_eq!(done.total_bytes, data.len() as u64);
            assert_eq!(std::fs::read(&file_path).expect("read file"), data);
            // 交账后写任务随即摘句柄退出（respond 先达、摘除在其后一拍）——
            // 有界轮询，不赌调度窗口
            tokio::time::timeout(std::time::Duration::from_secs(5), async {
                while handler.file_handles.open_handle_count() > 0 {
                    tokio::time::sleep(std::time::Duration::from_millis(10)).await;
                }
            })
            .await
            .expect("write stream handle should be removed after done");
        }

        #[tokio::test]
        async fn rejects_write_outside_allowed_roots() {
            let allowed = tempfile::tempdir().expect("tempdir");
            let allowed_root = std::fs::canonicalize(allowed.path()).expect("canonical root");
            let outside = tempfile::tempdir().expect("tempdir");
            let outside_root = std::fs::canonicalize(outside.path()).expect("canonical root");
            let file_path = outside_root.join("denied.bin");

            let handler = FileSystemHandler::new(test_runtime_paths());
            let err = handler
                .write_stream(write_stream_params(&file_path, "ws-denied", &allowed_root))
                .await
                .expect_err("write outside the sandbox should be rejected");

            // Seatbelt 拒绝 → EPERM → invalid_request（打开失败同步以 RPC 错误
            // 返回，与非沙箱路径语义一致）
            assert_eq!(err.code, -32600);
            assert!(!file_path.exists());
            assert_eq!(handler.file_handles.open_handle_count(), 0);
        }

        #[tokio::test]
        async fn close_aborts_stream_and_keeps_partial_file() {
            let temp_dir = tempfile::tempdir().expect("tempdir");
            let root = std::fs::canonicalize(temp_dir.path()).expect("canonical root");
            let file_path = root.join("aborted.bin");

            let handler = FileSystemHandler::new(test_runtime_paths());
            handler
                .write_stream(write_stream_params(&file_path, "ws-int", &root))
                .await
                .expect("open write stream");
            push_chunk(&handler, "ws-int", 0, b"half", false).await;

            // fs/close 对写流句柄即中止（v1.12 语义翻转：不删半成品——
            // 文件留在盘上，为断点续传让路）
            handler
                .close(FsCloseParams {
                    handle_id: "ws-int".to_string(),
                })
                .await
                .expect("close");
            await_file_content(&file_path, b"half").await;

            let err = handler
                .write_stream_done(FsWriteStreamDoneParams {
                    handle_id: "ws-int".to_string(),
                })
                .await
                .expect_err("done after abort should fail");
            assert_eq!(err.code, -32004);
        }

        #[tokio::test]
        async fn done_reports_seq_violation_and_keeps_partial_file() {
            let temp_dir = tempfile::tempdir().expect("tempdir");
            let root = std::fs::canonicalize(temp_dir.path()).expect("canonical root");
            let file_path = root.join("out-of-order.bin");

            let handler = FileSystemHandler::new(test_runtime_paths());
            handler
                .write_stream(write_stream_params(&file_path, "ws-ooo", &root))
                .await
                .expect("open write stream");
            push_chunk(&handler, "ws-ooo", 0, b"aa", false).await;
            push_chunk(&handler, "ws-ooo", 2, b"bb", true).await;

            let err = handler
                .write_stream_done(FsWriteStreamDoneParams {
                    handle_id: "ws-ooo".to_string(),
                })
                .await
                .expect_err("done should report the seq violation");
            assert_eq!(err.code, -32600);
            assert!(err.message.contains("expected seq 1, got 2"));
            assert_eq!(std::fs::read(&file_path).expect("partial file stays"), b"aa");
        }

        #[tokio::test]
        async fn done_without_eof_fails_and_keeps_partial_file() {
            let temp_dir = tempfile::tempdir().expect("tempdir");
            let root = std::fs::canonicalize(temp_dir.path()).expect("canonical root");
            let file_path = root.join("no-eof.bin");

            let handler = FileSystemHandler::new(test_runtime_paths());
            handler
                .write_stream(write_stream_params(&file_path, "ws-noeof", &root))
                .await
                .expect("open write stream");
            push_chunk(&handler, "ws-noeof", 0, b"half", false).await;

            let err = handler
                .write_stream_done(FsWriteStreamDoneParams {
                    handle_id: "ws-noeof".to_string(),
                })
                .await
                .expect_err("done without eof should fail");
            assert_eq!(err.code, -32600);
            assert!(err.message.contains("without an eof chunk"));
            assert_eq!(std::fs::read(&file_path).expect("partial file stays"), b"half");
        }
    }
}
