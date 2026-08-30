use std::io;

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD;
use nova_executor_protocol::JSONRPCErrorError;
use nova_executor_utils_path_uri::PathUri;
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;

use crate::CopyOptions;
use crate::CreateDirectoryOptions;
use crate::ExecServerRuntimePaths;
use crate::ExecutorFileSystem;
use crate::FileSystemSandboxContext;
use crate::GetMetadataOptions;
use crate::ReadFileOptions;
use crate::RemoveOptions;
use crate::WriteFileOptions;
use crate::file_read::DEFAULT_READ_STREAM_BLOCK_SIZE;
use crate::file_read::FileReadHandleManager;
use crate::file_read::MAX_READ_STREAM_BLOCK_SIZE;
use crate::file_read::stream_file_blocks;
use crate::file_write::FileWriteHandleManager;
use crate::file_write::FileWriteSandboxed;
use crate::file_write::SandboxedWriteCommand;
use crate::fs_sandbox::SandboxFsHelperWriteStream;
use crate::local_file_system::LocalFileSystem;
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
use crate::sandboxed_file_system::map_sandbox_error;

const MAX_FILE_READ_HANDLE_ID_BYTES: usize = 32;
const MAX_FILE_WRITE_HANDLE_ID_BYTES: usize = 32;
// Each read-directory entry needs four JSON values. Keep same-version
// producers comfortably below the shared 256K-value decoder budget.
const MAX_READ_DIRECTORY_ENTRIES: usize = 50_000;
/// 沙箱化写流的命令通道容量：满时背压经 RPC 层自然传导（helper 持续消费）
const SANDBOXED_WRITE_COMMAND_QUEUE: usize = 8;

#[derive(Clone)]
pub(crate) struct FileSystemHandler {
    file_system: LocalFileSystem,
    file_reads: FileReadHandleManager,
    file_writes: FileWriteHandleManager,
    notifications: Option<RpcNotificationSender>,
}

impl FileSystemHandler {
    pub(crate) fn new(runtime_paths: ExecServerRuntimePaths) -> Self {
        Self {
            file_system: LocalFileSystem::with_runtime_paths(runtime_paths),
            file_reads: FileReadHandleManager::default(),
            file_writes: FileWriteHandleManager::default(),
            notifications: None,
        }
    }

    pub(crate) fn with_notifications(mut self, notifications: RpcNotificationSender) -> Self {
        self.notifications = Some(notifications);
        self
    }

    pub(crate) async fn shutdown(&self) {
        self.file_reads.close_all().await;
        self.file_writes.close_all().await;
    }

    pub(crate) async fn open(
        &self,
        params: FsOpenParams,
    ) -> Result<FsOpenResponse, JSONRPCErrorError> {
        validate_file_read_handle_id(&params.handle_id)?;
        let file = self
            .file_system
            .open_file_for_read(&params.path, params.sandbox.as_ref())
            .await
            .map_err(map_fs_error)?;
        let handle_id = self
            .file_reads
            .open(params.handle_id, file)
            .await
            .map_err(map_fs_error)?;
        Ok(FsOpenResponse { handle_id })
    }

    pub(crate) async fn read_block(
        &self,
        params: FsReadBlockParams,
    ) -> Result<FsReadBlockResponse, JSONRPCErrorError> {
        validate_file_read_handle_id(&params.handle_id)?;
        let block = self
            .file_reads
            .read_block(&params.handle_id, params.offset, params.len)
            .await
            .map_err(map_fs_error)?;
        Ok(FsReadBlockResponse {
            chunk: block.bytes.into(),
            eof: block.eof,
        })
    }

    pub(crate) async fn close(
        &self,
        params: FsCloseParams,
    ) -> Result<FsCloseResponse, JSONRPCErrorError> {
        validate_file_read_handle_id(&params.handle_id)?;
        self.file_reads.close(&params.handle_id).await;
        // 对写流句柄的 close 即中止：删除未完成流的半截文件
        self.file_writes.abort(&params.handle_id).await;
        Ok(FsCloseResponse {})
    }

    pub(crate) async fn read_stream(
        &self,
        params: FsReadStreamParams,
    ) -> Result<FsReadStreamResponse, JSONRPCErrorError> {
        validate_file_read_handle_id(&params.handle_id)?;
        let block_size = params
            .block_size
            .unwrap_or(DEFAULT_READ_STREAM_BLOCK_SIZE)
            .clamp(1, MAX_READ_STREAM_BLOCK_SIZE);

        // 平台沙箱上下文对调用方透明：get_metadata 走一次性沙箱 helper，
        // open_file_for_read 经沙箱开门把 fd/handle 传回本进程（见
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

        let file = self
            .file_system
            .open_file_for_read(&params.path, params.sandbox.as_ref())
            .await
            .map_err(map_fs_error)?;
        let handle_id = self
            .file_reads
            .open(params.handle_id.clone(), file)
            .await
            .map_err(map_fs_error)?;

        // 启动后台流式读取任务
        let notifications = self.notifications.clone();
        let file_reads = self.file_reads.clone();
        let handle_id_clone = handle_id.clone();
        let offset = params.offset;
        let len = params.len;
        tokio::spawn(async move {
            let emit_notifications = notifications.clone();
            let emit_handle_id = handle_id_clone.clone();
            let (total_bytes, error) = stream_file_blocks(
                &file_reads,
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

            file_reads.close(&handle_id_clone).await;
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

    /// `fs/writeStream`：打开（创建/截断）目标文件并注册写流句柄，
    /// 随后客户端经 `fs/writeStream/chunk` 通知分片推数据。
    pub(crate) async fn write_stream(
        &self,
        params: FsWriteStreamParams,
    ) -> Result<FsWriteStreamResponse, JSONRPCErrorError> {
        validate_file_write_handle_id(&params.handle_id)?;

        // 平台沙箱上下文：文件写入委托给长命沙箱 helper 子进程，
        // 线上 writeStream/chunk/done 三件套形状不变
        if params
            .sandbox
            .as_ref()
            .is_some_and(FileSystemSandboxContext::should_run_in_sandbox)
        {
            return self.write_stream_sandboxed(params).await;
        }

        let file = self
            .file_system
            .open_file_for_write(&params.path, params.sandbox.as_ref())
            .await
            .map_err(map_fs_error)?;
        let handle_id = self
            .file_writes
            .open(params.handle_id, params.path, file)
            .await
            .map_err(map_fs_error)?;
        Ok(FsWriteStreamResponse { handle_id })
    }

    /// fs/writeStream 的沙箱路径：启动长命 fs_helper 沙箱子进程持续写文件，
    /// 后台任务（supervisor）把客户端的 chunk/done 原样转发给 helper 并收最终确认。
    ///
    /// 生命周期：流句柄注册进 `file_writes`（Sandboxed 形态），客户端 fs/close
    /// 与连接关闭（close_all）都会取消中断令牌 → supervisor 击杀 helper 并经
    /// 一次性沙箱 helper 删除半截文件；正常 done 后 supervisor 等 helper 退出
    /// 回收——两条路径都不留孤儿。
    async fn write_stream_sandboxed(
        &self,
        params: FsWriteStreamParams,
    ) -> Result<FsWriteStreamResponse, JSONRPCErrorError> {
        // 调用方已判别 should_run_in_sandbox，这里必然带平台沙箱上下文
        let sandbox = params.sandbox.clone().ok_or_else(|| {
            internal_error("sandboxed write stream requires a sandbox context".to_string())
        })?;
        let path = params.path.clone();
        let handle_id = params.handle_id.clone();

        let mut session = self
            .file_system
            .spawn_sandboxed_write_stream(&params)
            .await
            .map_err(map_fs_error)?;
        // 启动握手：打开失败等请求级错误在此同步返回（与非沙箱路径语义一致）
        let response = match session.write_start().await {
            Ok(response) => response,
            Err(err) => {
                session.finish().await; // 击杀并回收 helper
                return Err(err);
            }
        };

        // 启动 supervisor：独占 helper 会话，负责命令转发、中断清理与 helper 回收
        let (commands_tx, commands_rx) = mpsc::channel(SANDBOXED_WRITE_COMMAND_QUEUE);
        let cancel = CancellationToken::new();
        let supervisor = tokio::spawn(run_sandboxed_write_stream(
            session,
            self.file_system.clone(),
            path,
            sandbox,
            commands_rx,
            cancel.clone(),
        ));
        let registered = self
            .file_writes
            .open_sandboxed(
                handle_id,
                FileWriteSandboxed {
                    commands: commands_tx,
                    cancel: cancel.clone(),
                    supervisor,
                },
            )
            .await;
        if let Err(err) = registered {
            // 并发重复句柄等：取消令牌让 supervisor 走中止清理（击杀 helper、
            // 删除半截文件），注册失败原样上报
            cancel.cancel();
            return Err(map_fs_error(err));
        }
        Ok(response)
    }

    /// `fs/writeStream/chunk` 是通知（无回执）：业务错误只在句柄状态机内
    /// 流转，由随后的 done 请求回报；这里恒成功，避免协议错误关闭连接。
    pub(crate) async fn write_stream_chunk(
        &self,
        params: FsWriteStreamChunkNotification,
    ) -> Result<(), String> {
        self.file_writes
            .write_chunk(
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
            .file_writes
            .finish(&params.handle_id)
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

/// 沙箱化 fs/writeStream 的后台任务（supervisor）：独占 helper 会话，
/// 把 chunk/finish 命令逐条转发给沙箱 helper；中断（fs/close、连接关闭）
/// 与异常（helper 死亡）负责击杀 helper 并删除半截文件。
///
/// 错误传播对齐非沙箱路径：chunk 转发失败不直接回报（通知无回执），
/// 记为终态错误，由随后的 finish 命令应答给 done 请求。
async fn run_sandboxed_write_stream(
    session: SandboxFsHelperWriteStream,
    file_system: LocalFileSystem,
    path: PathUri,
    sandbox: FileSystemSandboxContext,
    mut commands: mpsc::Receiver<SandboxedWriteCommand>,
    cancel: CancellationToken,
) {
    let mut session = Some(session);
    // 半截文件是否仍需 executor 侧清理（helper 内的业务失败已自行删除；
    // 成功收尾无半截——force 语义让重复删除为空操作）
    let mut partial_pending = true;
    // 首个不可恢复错误（helper 死亡等）：留待 finish 命令应答
    let mut terminal_error: Option<io::Error> = None;

    loop {
        tokio::select! {
            // biased：中断优先于继续转发，close 后不再向 helper 发块
            biased;
            _ = cancel.cancelled() => {
                cleanup_sandboxed_write_stream(
                    &file_system,
                    &path,
                    &sandbox,
                    session.take(),
                    partial_pending,
                )
                .await;
                return;
            }
            command = commands.recv() => {
                let Some(command) = command else {
                    // 句柄被移除且未走 cancel（防御性路径）：同中止语义
                    cleanup_sandboxed_write_stream(
                        &file_system,
                        &path,
                        &sandbox,
                        session.take(),
                        partial_pending,
                    )
                    .await;
                    return;
                };
                match command {
                    SandboxedWriteCommand::Chunk(chunk) => {
                        if terminal_error.is_some() {
                            // 失败终态：静默忽略后续块，等 finish 回报首个错误
                            continue;
                        }
                        let send = match session.as_mut() {
                            Some(session) => session.send_chunk(&chunk).await,
                            None => Err(internal_error(
                                "fs sandbox write helper session is closed".to_string(),
                            )),
                        };
                        if let Err(err) = send {
                            // stdin 断裂 = helper 已死亡：回收并删半截，
                            // 错误留待 finish 应答（done 回报）
                            cleanup_sandboxed_write_stream(
                                &file_system,
                                &path,
                                &sandbox,
                                session.take(),
                                /*remove_partial*/ true,
                            )
                            .await;
                            partial_pending = false;
                            terminal_error = Some(map_sandbox_error(err));
                        }
                    }
                    SandboxedWriteCommand::Finish { done, respond } => {
                        let result = if let Some(error) = terminal_error.take() {
                            Err(error)
                        } else if let Some(session) = session.as_mut() {
                            match session.send_finish(&done).await {
                                Ok(()) => session
                                    .read_done()
                                    .await
                                    .map(|done| done.total_bytes)
                                    .map_err(map_sandbox_error),
                                Err(err) => Err(map_sandbox_error(err)),
                            }
                        } else {
                            Err(io::Error::other(
                                "file write stream sandbox helper is gone",
                            ))
                        };
                        if result.is_ok() {
                            // 成功：文件已完整落盘，回收 helper 进程
                            if let Some(session) = session.take() {
                                session.finish().await;
                            }
                        } else {
                            // 失败收尾：helper 业务错误已在 helper 内删过半截，
                            // helper 死亡/帧损坏则这里补删（force 容忍重复）
                            cleanup_sandboxed_write_stream(
                                &file_system,
                                &path,
                                &sandbox,
                                session.take(),
                                /*remove_partial*/ true,
                            )
                            .await;
                        }
                        let _ = respond.send(result);
                        return;
                    }
                }
            }
        }
    }
}

/// 沙箱写流的中断/异常清理：先击杀并回收 helper，再经一次性沙箱 helper
/// 删除半截文件——删除与写入走同一沙箱授权面，executor 主进程不越权直删。
async fn cleanup_sandboxed_write_stream(
    file_system: &LocalFileSystem,
    path: &PathUri,
    sandbox: &FileSystemSandboxContext,
    session: Option<SandboxFsHelperWriteStream>,
    remove_partial: bool,
) {
    if let Some(session) = session {
        session.finish().await;
    }
    if remove_partial
        && let Err(err) = file_system
            .remove(
                path,
                RemoveOptions {
                    recursive: false,
                    force: true,
                    follow_symlinks: true,
                },
                Some(sandbox),
            )
            .await
    {
        tracing::warn!("failed to remove partial sandboxed write stream file `{path}`: {err}");
    }
}

fn validate_file_read_handle_id(handle_id: &str) -> Result<(), JSONRPCErrorError> {
    if handle_id.len() > MAX_FILE_READ_HANDLE_ID_BYTES {
        return Err(invalid_request(format!(
            "file read handle ID must not exceed {MAX_FILE_READ_HANDLE_ID_BYTES} bytes"
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
    use nova_executor_protocol_core::models::PermissionProfile;
    use nova_executor_protocol_core::permissions::FileSystemAccessMode;
    use nova_executor_protocol_core::permissions::FileSystemPath;
    use nova_executor_protocol_core::permissions::FileSystemSandboxEntry;
    use nova_executor_protocol_core::permissions::FileSystemSandboxPolicy;
    use nova_executor_protocol_core::permissions::NetworkSandboxPolicy;
    use nova_executor_protocol_core::protocol::NetworkAccess;
    use nova_executor_protocol_core::protocol::SandboxPolicy;
    use nova_executor_utils_absolute_path::AbsolutePathBuf;
    use nova_executor_utils_path_uri::PathUri;
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
    async fn write_stream_close_aborts_and_removes_partial_file() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let handler = FileSystemHandler::new(test_runtime_paths());
        let native_path = temp_dir.path().join("aborted.bin");
        let path = PathUri::from_host_native_path(&native_path).expect("path URI");

        handler
            .write_stream(FsWriteStreamParams {
                handle_id: "w1".to_string(),
                path,
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

        // fs/close 对写流句柄即中止：半截文件删除
        handler
            .close(FsCloseParams {
                handle_id: "w1".to_string(),
            })
            .await
            .expect("close");
        assert!(!native_path.exists(), "partial file should be removed");

        let err = handler
            .write_stream_done(FsWriteStreamDoneParams {
                handle_id: "w1".to_string(),
            })
            .await
            .expect_err("done after abort should fail");
        assert_eq!(err.code, -32004);
    }

    #[tokio::test]
    async fn write_stream_done_without_eof_fails_and_removes_partial_file() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let handler = FileSystemHandler::new(test_runtime_paths());
        let native_path = temp_dir.path().join("no-eof.bin");
        let path = PathUri::from_host_native_path(&native_path).expect("path URI");

        handler
            .write_stream(FsWriteStreamParams {
                handle_id: "w1".to_string(),
                path,
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
        assert!(!native_path.exists(), "partial file should be removed");
    }

    #[tokio::test]
    async fn write_stream_out_of_order_chunk_fails_done_and_removes_partial_file() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let handler = FileSystemHandler::new(test_runtime_paths());
        let native_path = temp_dir.path().join("out-of-order.bin");
        let path = PathUri::from_host_native_path(&native_path).expect("path URI");

        handler
            .write_stream(FsWriteStreamParams {
                handle_id: "w1".to_string(),
                path,
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
        assert!(!native_path.exists(), "partial file should be removed");
    }

    #[tokio::test]
    async fn write_stream_shutdown_removes_partial_files() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let handler = FileSystemHandler::new(test_runtime_paths());
        let native_path = temp_dir.path().join("shutdown.bin");
        let path = PathUri::from_host_native_path(&native_path).expect("path URI");

        handler
            .write_stream(FsWriteStreamParams {
                handle_id: "w1".to_string(),
                path,
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
        assert!(!native_path.exists(), "partial file should be removed");
    }

    #[tokio::test]
    async fn open_file_for_write_still_rejects_platform_sandbox_context() {
        // RPC 层 fs/writeStream 已支持平台沙箱（走长命沙箱 helper）；这里守住
        // 的是进程内 file 句柄直开的兜底边界（句柄无法跨 helper 进程持有）
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let file_system = LocalFileSystem::with_runtime_paths(test_runtime_paths());
        let path = PathUri::from_host_native_path(temp_dir.path().join("sandboxed.bin"))
            .expect("path URI");
        let native_cwd =
            AbsolutePathBuf::from_absolute_path(temp_dir.path()).expect("absolute cwd");
        let policy = FileSystemSandboxPolicy::restricted(vec![FileSystemSandboxEntry {
            path: FileSystemPath::Path {
                path: native_cwd.into(),
            },
            access: FileSystemAccessMode::Write,
            missing_path_behavior: None,
        }]);
        let sandbox = FileSystemSandboxContext::from_permission_profile_with_cwd(
            PermissionProfile::from_runtime_permissions(&policy, NetworkSandboxPolicy::Restricted),
            PathUri::from_host_native_path(temp_dir.path()).expect("cwd URI"),
        );
        assert!(sandbox.should_run_in_sandbox());

        let err = file_system
            .open_file_for_write(&path, Some(&sandbox))
            .await
            .expect_err("direct open with platform sandbox should be rejected");
        assert_eq!(err.kind(), io::ErrorKind::InvalidInput);
        assert!(
            err.to_string()
                .contains("streaming file writes do not support platform sandboxing")
        );
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

        use nova_executor_protocol_core::models::PermissionProfile;
        use nova_executor_protocol_core::permissions::FileSystemAccessMode;
        use nova_executor_protocol_core::permissions::FileSystemPath;
        use nova_executor_protocol_core::permissions::FileSystemSandboxEntry;
        use nova_executor_protocol_core::permissions::FileSystemSandboxPolicy;
        use nova_executor_protocol_core::permissions::NetworkSandboxPolicy;
        use nova_executor_utils_absolute_path::AbsolutePathBuf;
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
                    crate::fs_helper::NOVA_EXECUTOR_FS_HELPER_ARG1,
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
            let sandbox = FileSystemSandboxContext::from_permission_profile_with_cwd(
                PermissionProfile::from_runtime_permissions(
                    &policy,
                    NetworkSandboxPolicy::Restricted,
                ),
                PathUri::from_host_native_path(root).expect("cwd URI"),
            );
            assert!(sandbox.should_run_in_sandbox());
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
                while handler.file_reads.open_handle_count().await > 0 {
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
            assert_eq!(handler.file_reads.open_handle_count().await, 0);
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
            assert_eq!(handler.file_reads.open_handle_count().await, 0);

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
    /// fs_helper 子进程（helper 即当前测试二进制，ctor 分派与读流模块相同）。
    #[cfg(target_os = "macos")]
    mod sandboxed_write_stream {
        use std::path::Path;

        use nova_executor_protocol_core::models::PermissionProfile;
        use nova_executor_protocol_core::permissions::FileSystemAccessMode;
        use nova_executor_protocol_core::permissions::FileSystemPath;
        use nova_executor_protocol_core::permissions::FileSystemSandboxEntry;
        use nova_executor_protocol_core::permissions::FileSystemSandboxPolicy;
        use nova_executor_protocol_core::permissions::NetworkSandboxPolicy;
        use nova_executor_utils_absolute_path::AbsolutePathBuf;
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
                    crate::fs_helper::NOVA_EXECUTOR_FS_HELPER_ARG1,
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
            let sandbox = FileSystemSandboxContext::from_permission_profile_with_cwd(
                PermissionProfile::from_runtime_permissions(
                    &policy,
                    NetworkSandboxPolicy::Restricted,
                ),
                PathUri::from_host_native_path(root).expect("cwd URI"),
            );
            assert!(sandbox.should_run_in_sandbox());
            sandbox
        }

        fn write_stream_params(path: &Path, handle_id: &str, root: &Path) -> FsWriteStreamParams {
            FsWriteStreamParams {
                handle_id: handle_id.to_string(),
                path: PathUri::from_host_native_path(path).expect("path URI"),
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
            // 握手成功 = helper 已在沙箱内创建/截断目标文件
            assert!(file_path.exists());

            // 64KB 分块推送（末块带 eof），经 supervisor 逐帧转发给 helper
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
            assert_eq!(handler.file_writes.open_handle_count().await, 0);
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
            assert_eq!(handler.file_writes.open_handle_count().await, 0);
        }

        #[tokio::test]
        async fn close_aborts_stream_and_removes_partial_file() {
            let temp_dir = tempfile::tempdir().expect("tempdir");
            let root = std::fs::canonicalize(temp_dir.path()).expect("canonical root");
            let file_path = root.join("aborted.bin");

            let handler = FileSystemHandler::new(test_runtime_paths());
            handler
                .write_stream(write_stream_params(&file_path, "ws-int", &root))
                .await
                .expect("open write stream");
            push_chunk(&handler, "ws-int", 0, b"half", false).await;

            // fs/close 对写流句柄即中止：击杀 helper 并经一次性沙箱 helper
            // 删除半截文件（abort 等 supervisor 收尾完成后才返回）
            handler
                .close(FsCloseParams {
                    handle_id: "ws-int".to_string(),
                })
                .await
                .expect("close");
            assert!(!file_path.exists(), "partial file should be removed");

            let err = handler
                .write_stream_done(FsWriteStreamDoneParams {
                    handle_id: "ws-int".to_string(),
                })
                .await
                .expect_err("done after abort should fail");
            assert_eq!(err.code, -32004);
        }

        #[tokio::test]
        async fn done_reports_seq_violation_and_removes_partial_file() {
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
            assert!(!file_path.exists(), "partial file should be removed");
        }

        #[tokio::test]
        async fn done_without_eof_fails_and_removes_partial_file() {
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
            assert!(!file_path.exists(), "partial file should be removed");
        }
    }
}
