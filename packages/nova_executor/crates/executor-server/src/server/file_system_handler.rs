use std::io;

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD;
use nova_executor_protocol::JSONRPCErrorError;

use crate::CopyOptions;
use crate::CreateDirectoryOptions;
use crate::ExecServerRuntimePaths;
use crate::ExecutorFileSystem;
use crate::RemoveOptions;
use crate::file_read::FileReadHandleManager;
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
use crate::protocol::FsReadStreamChunkNotification;
use crate::protocol::FsReadStreamDoneNotification;
use crate::protocol::FsReadStreamParams;
use crate::protocol::FsReadStreamResponse;
use crate::protocol::FsReadDirectoryEntry;
use crate::protocol::FsReadDirectoryParams;
use crate::protocol::FsReadDirectoryResponse;
use crate::protocol::FsReadFileParams;
use crate::protocol::FsReadFileResponse;
use crate::protocol::FsRemoveParams;
use crate::protocol::FsRemoveResponse;
use crate::protocol::FsWalkParams;
use crate::protocol::FsWalkResponse;
use crate::protocol::FsWriteFileParams;
use crate::protocol::FsWriteFileResponse;
use crate::rpc::RpcNotificationSender;
use crate::rpc::internal_error;
use crate::rpc::invalid_request;
use crate::rpc::not_found;

const MAX_FILE_READ_HANDLE_ID_BYTES: usize = 32;
// Each read-directory entry needs four JSON values. Keep same-version
// producers comfortably below the shared 256K-value decoder budget.
const MAX_READ_DIRECTORY_ENTRIES: usize = 50_000;

const DEFAULT_READ_STREAM_BLOCK_SIZE: usize = 256 * 1024; // 256KB
const MAX_READ_STREAM_BLOCK_SIZE: usize = 4 * 1024 * 1024; // 4MB

#[derive(Clone)]
pub(crate) struct FileSystemHandler {
    file_system: LocalFileSystem,
    file_reads: FileReadHandleManager,
    notifications: Option<RpcNotificationSender>,
}

impl FileSystemHandler {
    pub(crate) fn new(runtime_paths: ExecServerRuntimePaths) -> Self {
        Self {
            file_system: LocalFileSystem::with_runtime_paths(runtime_paths),
            file_reads: FileReadHandleManager::default(),
            notifications: None,
        }
    }

    pub(crate) fn with_notifications(mut self, notifications: RpcNotificationSender) -> Self {
        self.notifications = Some(notifications);
        self
    }

    pub(crate) async fn shutdown(&self) {
        self.file_reads.close_all().await;
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

        let metadata = self
            .file_system
            .get_metadata(&params.path, params.sandbox.as_ref())
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
            let mut seq = 0u64;
            let mut current_offset = offset;
            let mut total_bytes = 0u64;
            let mut error: Option<String> = None;

            loop {
                let remaining = len.map(|l| l.saturating_sub(total_bytes));
                let read_len = remaining
                    .map(|r| r.min(block_size as u64) as usize)
                    .unwrap_or(block_size);
                if read_len == 0 {
                    break;
                }

                match file_reads
                    .read_block(&handle_id_clone, current_offset, read_len)
                    .await
                {
                    Ok(block) => {
                        let bytes_len = block.bytes.len() as u64;
                        let eof = block.eof || remaining.is_some_and(|r| r <= bytes_len);
                        total_bytes = total_bytes.saturating_add(bytes_len);
                        current_offset = current_offset.saturating_add(bytes_len);

                        if let Some(notifications) = &notifications {
                            let _ = notifications
                                .notify(
                                    crate::protocol::FS_READ_STREAM_CHUNK_METHOD,
                                    &FsReadStreamChunkNotification {
                                        handle_id: handle_id_clone.clone(),
                                        seq,
                                        chunk: block.bytes.into(),
                                        eof,
                                    },
                                )
                                .await;
                        }

                        seq += 1;
                        if eof {
                            break;
                        }
                    }
                    Err(err) => {
                        error = Some(err.to_string());
                        break;
                    }
                }
            }

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
            .read_file(&params.path, params.sandbox.as_ref())
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
            .write_file(&params.path, bytes, params.sandbox.as_ref())
            .await
            .map_err(map_fs_error)?;
        Ok(FsWriteFileResponse {})
    }

    pub(crate) async fn create_directory(
        &self,
        params: FsCreateDirectoryParams,
    ) -> Result<FsCreateDirectoryResponse, JSONRPCErrorError> {
        let recursive = params.recursive.unwrap_or(true);
        self.file_system
            .create_directory(
                &params.path,
                CreateDirectoryOptions { recursive },
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
            .get_metadata(&params.path, params.sandbox.as_ref())
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
                RemoveOptions { recursive, force },
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

fn validate_file_read_handle_id(handle_id: &str) -> Result<(), JSONRPCErrorError> {
    if handle_id.len() > MAX_FILE_READ_HANDLE_ID_BYTES {
        return Err(invalid_request(format!(
            "file read handle ID must not exceed {MAX_FILE_READ_HANDLE_ID_BYTES} bytes"
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
    use nova_executor_protocol_core::protocol::NetworkAccess;
    use nova_executor_protocol_core::protocol::SandboxPolicy;
    use nova_executor_utils_path_uri::PathUri;
    use pretty_assertions::assert_eq;

    use super::*;
    use crate::FileSystemSandboxContext;
    use crate::protocol::FsReadFileParams;
    use crate::protocol::FsWriteFileParams;

    #[tokio::test]
    async fn no_platform_sandbox_policies_do_not_require_configured_sandbox_helper() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let runtime_paths = ExecServerRuntimePaths::new(
            std::env::current_exe().expect("current exe"),
            /*codex_linux_sandbox_exe*/ None,
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
                    sandbox: Some(sandbox_context(sandbox_policy)),
                })
                .await
                .expect("read file");

            assert_eq!(response.data_base64, STANDARD.encode("ok"));
        }
    }
}
