use base64::Engine as _;
use base64::engine::general_purpose::STANDARD;
use nova_executor_protocol::JSONRPCErrorError;
use serde::Deserialize;
use serde::Serialize;
use tokio::io;
use tokio::io::AsyncWriteExt;
use tokio::io::Lines;

use crate::CopyOptions;
use crate::CreateDirectoryOptions;
use crate::ExecutorFileSystem;
use crate::GetMetadataOptions;
use crate::ReadFileOptions;
use crate::RemoveOptions;
use crate::WriteFileOptions;
use crate::file_write::FileWriteHandleManager;
use crate::local_file_system::DirectFileSystem;
use crate::protocol::FS_CANONICALIZE_METHOD;
use crate::protocol::FS_COPY_METHOD;
use crate::protocol::FS_CREATE_DIRECTORY_METHOD;
use crate::protocol::FS_GET_METADATA_METHOD;
use crate::protocol::FS_OPEN_METHOD;
use crate::protocol::FS_READ_DIRECTORY_METHOD;
use crate::protocol::FS_READ_FILE_METHOD;
use crate::protocol::FS_REMOVE_METHOD;
use crate::protocol::FS_WALK_METHOD;
use crate::protocol::FS_WRITE_FILE_METHOD;
use crate::protocol::FS_WRITE_STREAM_DONE_METHOD;
use crate::protocol::FS_WRITE_STREAM_METHOD;
use crate::protocol::FsCanonicalizeParams;
use crate::protocol::FsCanonicalizeResponse;
use crate::protocol::FsCopyParams;
use crate::protocol::FsCopyResponse;
use crate::protocol::FsCreateDirectoryParams;
use crate::protocol::FsCreateDirectoryResponse;
use crate::protocol::FsGetMetadataParams;
use crate::protocol::FsGetMetadataResponse;
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
use crate::protocol::FsWriteStreamChunkNotification;
use crate::protocol::FsWriteStreamDoneParams;
use crate::protocol::FsWriteStreamDoneResponse;
use crate::protocol::FsWriteStreamParams;
use crate::protocol::FsWriteStreamResponse;
use crate::rpc::internal_error;
use crate::rpc::invalid_request;
use crate::rpc::not_found;

pub const NOVA_EXECUTOR_FS_HELPER_ARG1: &str = "--codex-run-as-fs-helper";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "operation", content = "params")]
pub(crate) enum FsHelperRequest {
    #[serde(rename = "fs/readFile")]
    ReadFile(FsReadFileParams),
    #[serde(rename = "fs/writeFile")]
    WriteFile(FsWriteFileParams),
    #[serde(rename = "fs/createDirectory")]
    CreateDirectory(FsCreateDirectoryParams),
    #[serde(rename = "fs/getMetadata")]
    GetMetadata(FsGetMetadataParams),
    #[serde(rename = "fs/canonicalize")]
    Canonicalize(FsCanonicalizeParams),
    #[serde(rename = "fs/readDirectory")]
    ReadDirectory(FsReadDirectoryParams),
    #[serde(rename = "fs/walk")]
    Walk(FsWalkParams),
    #[serde(rename = "fs/remove")]
    Remove(FsRemoveParams),
    #[serde(rename = "fs/copy")]
    Copy(FsCopyParams),
    /// 一次性开门：helper 在沙箱内 open 后把 fd/handle 传回 executor
    /// （见 [`crate::sandboxed_file_open`]），不走普通的一次性响应执行体。
    #[serde(rename = "fs/open")]
    Open(FsReadFileParams),
    /// 长命流式写：helper 进程活到流结束，stdin 逐行收 chunk/finish 事件帧
    /// （见 [`run_write_stream_request`]），最终确认走一次性响应信封。
    #[serde(rename = "fs/writeStream")]
    WriteStream(FsWriteStreamParams),
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "status", content = "payload", rename_all = "camelCase")]
pub(crate) enum FsHelperResponse {
    Ok(FsHelperPayload),
    Error(JSONRPCErrorError),
}

/// 一次性开门请求的应答载荷：Unix 直接经 SCM_RIGHTS 传 fd（载荷为空）；
/// Windows 由父进程按 process_id + file_handle 从 helper 进程复制句柄。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct FsHelperOpenResponse {
    // Windows 从 helper 进程复制句柄。
    #[cfg(windows)]
    pub(crate) process_id: u32,
    // Unix 直接传递 fd，无需回传句柄值。
    #[cfg(windows)]
    pub(crate) file_handle: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "operation", content = "response")]
pub(crate) enum FsHelperPayload {
    #[serde(rename = "fs/readFile")]
    ReadFile(FsReadFileResponse),
    #[serde(rename = "fs/writeFile")]
    WriteFile(FsWriteFileResponse),
    #[serde(rename = "fs/createDirectory")]
    CreateDirectory(FsCreateDirectoryResponse),
    #[serde(rename = "fs/getMetadata")]
    GetMetadata(FsGetMetadataResponse),
    #[serde(rename = "fs/canonicalize")]
    Canonicalize(FsCanonicalizeResponse),
    #[serde(rename = "fs/readDirectory")]
    ReadDirectory(FsReadDirectoryResponse),
    #[serde(rename = "fs/walk")]
    Walk(FsWalkResponse),
    #[serde(rename = "fs/remove")]
    Remove(FsRemoveResponse),
    #[serde(rename = "fs/copy")]
    Copy(FsCopyResponse),
    /// 一次性开门的应答载荷（Unix 为空——fd 经 SCM_RIGHTS 带外传递）
    #[serde(rename = "fs/open")]
    Open(FsHelperOpenResponse),
    /// 长命流式写的启动握手载荷（仅作为 helper stdout 的首行出现）
    #[serde(rename = "fs/writeStream")]
    WriteStream(FsWriteStreamResponse),
    /// 长命流式写的最终确认载荷（helper stdout 的末行，对应 fs/writeStream/done）
    #[serde(rename = "fs/writeStream/done")]
    WriteStreamDone(FsWriteStreamDoneResponse),
}

/// 长命 fs/writeStream helper 的 stdin 事件帧（NDJSON，一行一事件）。
/// 与线上 fs/writeStream/chunk 通知、fs/writeStream/done 请求同构，
/// executor 只做转发；helper 收齐后回传一行最终确认（见
/// [`run_write_stream_request`]）。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "event", content = "data", rename_all = "camelCase")]
pub(crate) enum FsHelperWriteStreamEvent {
    #[serde(rename = "chunk")]
    Chunk(FsWriteStreamChunkNotification),
    #[serde(rename = "finish")]
    Finish(FsWriteStreamDoneParams),
}

impl FsHelperPayload {
    fn operation(&self) -> &'static str {
        match self {
            Self::ReadFile(_) => FS_READ_FILE_METHOD,
            Self::WriteFile(_) => FS_WRITE_FILE_METHOD,
            Self::CreateDirectory(_) => FS_CREATE_DIRECTORY_METHOD,
            Self::GetMetadata(_) => FS_GET_METADATA_METHOD,
            Self::Canonicalize(_) => FS_CANONICALIZE_METHOD,
            Self::ReadDirectory(_) => FS_READ_DIRECTORY_METHOD,
            Self::Walk(_) => FS_WALK_METHOD,
            Self::Remove(_) => FS_REMOVE_METHOD,
            Self::Copy(_) => FS_COPY_METHOD,
            Self::Open(_) => FS_OPEN_METHOD,
            Self::WriteStream(_) => FS_WRITE_STREAM_METHOD,
            Self::WriteStreamDone(_) => FS_WRITE_STREAM_DONE_METHOD,
        }
    }

    pub(crate) fn expect_read_file(self) -> Result<FsReadFileResponse, JSONRPCErrorError> {
        match self {
            Self::ReadFile(response) => Ok(response),
            other => Err(unexpected_response(FS_READ_FILE_METHOD, other.operation())),
        }
    }

    pub(crate) fn expect_write_file(self) -> Result<FsWriteFileResponse, JSONRPCErrorError> {
        match self {
            Self::WriteFile(response) => Ok(response),
            other => Err(unexpected_response(FS_WRITE_FILE_METHOD, other.operation())),
        }
    }

    pub(crate) fn expect_create_directory(
        self,
    ) -> Result<FsCreateDirectoryResponse, JSONRPCErrorError> {
        match self {
            Self::CreateDirectory(response) => Ok(response),
            other => Err(unexpected_response(
                FS_CREATE_DIRECTORY_METHOD,
                other.operation(),
            )),
        }
    }

    pub(crate) fn expect_get_metadata(self) -> Result<FsGetMetadataResponse, JSONRPCErrorError> {
        match self {
            Self::GetMetadata(response) => Ok(response),
            other => Err(unexpected_response(
                FS_GET_METADATA_METHOD,
                other.operation(),
            )),
        }
    }

    pub(crate) fn expect_canonicalize(self) -> Result<FsCanonicalizeResponse, JSONRPCErrorError> {
        match self {
            Self::Canonicalize(response) => Ok(response),
            other => Err(unexpected_response(
                FS_CANONICALIZE_METHOD,
                other.operation(),
            )),
        }
    }

    pub(crate) fn expect_read_directory(
        self,
    ) -> Result<FsReadDirectoryResponse, JSONRPCErrorError> {
        match self {
            Self::ReadDirectory(response) => Ok(response),
            other => Err(unexpected_response(
                FS_READ_DIRECTORY_METHOD,
                other.operation(),
            )),
        }
    }

    pub(crate) fn expect_walk(self) -> Result<FsWalkResponse, JSONRPCErrorError> {
        match self {
            Self::Walk(response) => Ok(response),
            other => Err(unexpected_response(FS_WALK_METHOD, other.operation())),
        }
    }

    pub(crate) fn expect_remove(self) -> Result<FsRemoveResponse, JSONRPCErrorError> {
        match self {
            Self::Remove(response) => Ok(response),
            other => Err(unexpected_response(FS_REMOVE_METHOD, other.operation())),
        }
    }

    pub(crate) fn expect_copy(self) -> Result<FsCopyResponse, JSONRPCErrorError> {
        match self {
            Self::Copy(response) => Ok(response),
            other => Err(unexpected_response(FS_COPY_METHOD, other.operation())),
        }
    }

    pub(crate) fn expect_open(self) -> Result<FsHelperOpenResponse, JSONRPCErrorError> {
        match self {
            Self::Open(response) => Ok(response),
            other => Err(unexpected_response(FS_OPEN_METHOD, other.operation())),
        }
    }

    pub(crate) fn expect_write_stream(self) -> Result<FsWriteStreamResponse, JSONRPCErrorError> {
        match self {
            Self::WriteStream(response) => Ok(response),
            other => Err(unexpected_response(
                FS_WRITE_STREAM_METHOD,
                other.operation(),
            )),
        }
    }

    pub(crate) fn expect_write_stream_done(
        self,
    ) -> Result<FsWriteStreamDoneResponse, JSONRPCErrorError> {
        match self {
            Self::WriteStreamDone(response) => Ok(response),
            other => Err(unexpected_response(
                FS_WRITE_STREAM_DONE_METHOD,
                other.operation(),
            )),
        }
    }
}

fn unexpected_response(expected: &str, actual: &str) -> JSONRPCErrorError {
    internal_error(format!(
        "unexpected fs sandbox helper response: expected {expected}, got {actual}"
    ))
}

pub(crate) async fn run_direct_request(
    request: FsHelperRequest,
) -> Result<FsHelperPayload, JSONRPCErrorError> {
    let file_system = DirectFileSystem;
    match request {
        FsHelperRequest::ReadFile(params) => {
            let data = file_system
                .read_file(
                    &params.path,
                    ReadFileOptions {
                        follow_symlinks: params.follow_symlinks.unwrap_or(true),
                    },
                    /*sandbox*/ None,
                )
                .await
                .map_err(map_fs_error)?;
            Ok(FsHelperPayload::ReadFile(FsReadFileResponse {
                data_base64: STANDARD.encode(data),
            }))
        }
        FsHelperRequest::WriteFile(params) => {
            let bytes = STANDARD.decode(params.data_base64).map_err(|err| {
                invalid_request(format!(
                    "{FS_WRITE_FILE_METHOD} requires valid base64 dataBase64: {err}"
                ))
            })?;
            file_system
                .write_file(
                    &params.path,
                    bytes,
                    WriteFileOptions {
                        follow_symlinks: params.follow_symlinks.unwrap_or(true),
                    },
                    /*sandbox*/ None,
                )
                .await
                .map_err(map_fs_error)?;
            Ok(FsHelperPayload::WriteFile(FsWriteFileResponse {}))
        }
        FsHelperRequest::CreateDirectory(params) => {
            file_system
                .create_directory(
                    &params.path,
                    CreateDirectoryOptions {
                        recursive: params.recursive.unwrap_or(true),
                        follow_symlinks: params.follow_symlinks.unwrap_or(true),
                    },
                    /*sandbox*/ None,
                )
                .await
                .map_err(map_fs_error)?;
            Ok(FsHelperPayload::CreateDirectory(
                FsCreateDirectoryResponse {},
            ))
        }
        FsHelperRequest::GetMetadata(params) => {
            let metadata = file_system
                .get_metadata(
                    &params.path,
                    GetMetadataOptions {
                        follow_symlinks: params.follow_symlinks.unwrap_or(true),
                    },
                    /*sandbox*/ None,
                )
                .await
                .map_err(map_fs_error)?;
            Ok(FsHelperPayload::GetMetadata(FsGetMetadataResponse {
                is_directory: metadata.is_directory,
                is_file: metadata.is_file,
                is_symlink: metadata.is_symlink,
                size: metadata.size,
                created_at_ms: metadata.created_at_ms,
                modified_at_ms: metadata.modified_at_ms,
            }))
        }
        FsHelperRequest::Canonicalize(params) => {
            let path = file_system
                .canonicalize(&params.path, /*sandbox*/ None)
                .await
                .map_err(map_fs_error)?;
            Ok(FsHelperPayload::Canonicalize(FsCanonicalizeResponse {
                path,
            }))
        }
        FsHelperRequest::ReadDirectory(params) => {
            let entries = file_system
                .read_directory(&params.path, /*sandbox*/ None)
                .await
                .map_err(map_fs_error)?
                .into_iter()
                .map(|entry| FsReadDirectoryEntry {
                    file_name: entry.file_name,
                    is_directory: entry.is_directory,
                    is_file: entry.is_file,
                })
                .collect();
            Ok(FsHelperPayload::ReadDirectory(FsReadDirectoryResponse {
                entries,
            }))
        }
        FsHelperRequest::Walk(params) => {
            let outcome = file_system
                .walk(&params.path, params.options, /*sandbox*/ None)
                .await
                .map_err(map_fs_error)?;
            Ok(FsHelperPayload::Walk(outcome))
        }
        FsHelperRequest::Remove(params) => {
            file_system
                .remove(
                    &params.path,
                    RemoveOptions {
                        recursive: params.recursive.unwrap_or(true),
                        force: params.force.unwrap_or(true),
                        follow_symlinks: params.follow_symlinks.unwrap_or(true),
                    },
                    /*sandbox*/ None,
                )
                .await
                .map_err(map_fs_error)?;
            Ok(FsHelperPayload::Remove(FsRemoveResponse {}))
        }
        FsHelperRequest::Copy(params) => {
            file_system
                .copy(
                    &params.source_path,
                    &params.destination_path,
                    CopyOptions {
                        recursive: params.recursive,
                    },
                    /*sandbox*/ None,
                )
                .await
                .map_err(map_fs_error)?;
            Ok(FsHelperPayload::Copy(FsCopyResponse {}))
        }
        FsHelperRequest::Open(_) => Err(invalid_request(
            "opening a file requires descriptor handoff".to_string(),
        )),
        FsHelperRequest::WriteStream(_) => Err(internal_error(
            "fs/writeStream requires the streaming fs helper entrypoint".to_string(),
        )),
    }
}

pub(crate) fn map_fs_error(err: io::Error) -> JSONRPCErrorError {
    match err.kind() {
        io::ErrorKind::NotFound => not_found(err.to_string()),
        io::ErrorKind::InvalidInput | io::ErrorKind::PermissionDenied => {
            invalid_request(err.to_string())
        }
        _ => internal_error(err.to_string()),
    }
}

/// 长命 fs/writeStream 沙箱 helper 的执行体。
///
/// 帧协议（NDJSON，一行一条）：
/// - stdin：首行为 `FsHelperRequest::WriteStream` 请求帧（由入口先行消费，
///   这里拿到的 `stdin` 从第二行开始），随后逐行 `FsHelperWriteStreamEvent`
///   ——`Chunk` 与线上 fs/writeStream/chunk 通知同构，`Finish` 对应
///   fs/writeStream/done 请求；stdin EOF（executor 消失/中止）视为中止。
/// - stdout：首行为启动握手——文件打开（创建/截断）成功后写
///   `FsHelperResponse::Ok(WriteStream(FsWriteStreamResponse))`，打开失败则写
///   `FsHelperResponse::Error`（与一次性请求的错误信封同构），executor 据此把
///   打开失败映射回 writeStream 的 RPC 错误，与非沙箱路径语义一致；
///   收到 `Finish` 后写末行最终确认（成功 `Ok(WriteStreamDone(..))` 携带总字节数，
///   业务失败 `Error(..)`），写完后 helper 自行退出。
///
/// seq 严格序 / eof 校验 / 半截文件删除等流语义复用 [`FileWriteHandleManager`]
/// （与 executor 自写路径同一份实现），保证两种执行体线上行为一致。
/// helper 进程已在平台沙箱内运行，这里直连本地文件系统（不再叠加沙箱上下文）。
pub(crate) async fn run_write_stream_request<R, W>(
    params: FsWriteStreamParams,
    stdin: &mut Lines<R>,
    stdout: &mut W,
) -> io::Result<()>
where
    R: io::AsyncBufRead + Unpin,
    W: io::AsyncWrite + Unpin,
{
    let file_system = DirectFileSystem;
    let file = match file_system
        .open_file_for_write(&params.path, /*sandbox*/ None)
        .await
    {
        Ok(file) => file,
        Err(err) => {
            write_stdout_line(stdout, &FsHelperResponse::Error(map_fs_error(err))).await?;
            return Ok(());
        }
    };
    // 与 executor 自写路径一致的句柄与流语义（seq/eof/块长校验、失败即删半截）
    let file_writes = FileWriteHandleManager::default();
    let handle_id = params.handle_id.clone();
    file_writes
        .open(handle_id.clone(), params.path.clone(), file)
        .await?;
    write_stdout_line(
        stdout,
        &FsHelperResponse::Ok(FsHelperPayload::WriteStream(FsWriteStreamResponse {
            handle_id: handle_id.clone(),
        })),
    )
    .await?;

    let mut finish = None;
    while let Some(line) = stdin.next_line().await? {
        let event = match serde_json::from_str::<FsHelperWriteStreamEvent>(&line) {
            Ok(event) => event,
            Err(err) => {
                // 帧损坏属协议违约：回报错误并按中止语义清理半截文件
                write_stdout_line(
                    stdout,
                    &FsHelperResponse::Error(invalid_request(format!(
                        "failed to decode fs/writeStream helper event frame: {err}"
                    ))),
                )
                .await?;
                file_writes.abort(&handle_id).await;
                return Ok(());
            }
        };
        match event {
            FsHelperWriteStreamEvent::Chunk(chunk) => {
                file_writes
                    .write_chunk(
                        &chunk.handle_id,
                        chunk.seq,
                        chunk.chunk.into_inner(),
                        chunk.eof,
                    )
                    .await;
            }
            FsHelperWriteStreamEvent::Finish(done) => {
                finish = Some(done);
                break;
            }
        }
    }

    match finish {
        Some(done) => {
            // 收尾确认：流须已见 eof 块；成功回报总落盘字节数，失败回报首个错误
            // （业务失败的半截文件已由句柄管理器删除）
            let response = match file_writes.finish(&done.handle_id).await {
                Ok(total_bytes) => FsHelperResponse::Ok(FsHelperPayload::WriteStreamDone(
                    FsWriteStreamDoneResponse {
                        handle_id: done.handle_id,
                        total_bytes,
                    },
                )),
                Err(err) => FsHelperResponse::Error(map_fs_error(err)),
            };
            write_stdout_line(stdout, &response).await?;
        }
        None => {
            // stdin EOF 而未见 Finish：executor 消失/中止——按中止语义删除半截文件
            file_writes.abort(&handle_id).await;
        }
    }
    Ok(())
}

/// 写一行 NDJSON 帧并 flush（每帧即时送达，不能攒在缓冲里拖住推流）。
async fn write_stdout_line<W>(stdout: &mut W, message: &impl Serialize) -> io::Result<()>
where
    W: io::AsyncWrite + Unpin,
{
    let encoded = serde_json::to_vec(message)
        .map_err(|err| io::Error::new(io::ErrorKind::InvalidData, err))?;
    stdout.write_all(&encoded).await?;
    stdout.write_all(b"\n").await?;
    stdout.flush().await
}

#[cfg(test)]
mod tests {
    use nova_executor_utils_path_uri::PathUri;
    use pretty_assertions::assert_eq;
    use serde_json::json;
    use tokio::io::AsyncBufReadExt;

    use super::*;

    #[test]
    fn helper_protocol_uses_path_uris() -> serde_json::Result<()> {
        let local_path =
            PathUri::from_host_native_path(std::env::current_dir().expect("cwd").join("file"))
                .expect("path URI");
        let paths = [
            local_path,
            PathUri::parse("file://server/share/file").expect("path URI"),
        ];

        for path in paths {
            let expected_path = path.to_string();

            let request = serde_json::to_value(FsHelperRequest::WriteFile(FsWriteFileParams {
                path: path.clone(),
                data_base64: String::new(),
                follow_symlinks: None,
                sandbox: None,
            }))?;
            assert_eq!(
                request,
                json!({
                    "operation": FS_WRITE_FILE_METHOD,
                    "params": {
                        "path": expected_path.as_str(),
                        "dataBase64": "",
                        "sandbox": null,
                    },
                }),
            );
            let request_path = request["params"]["path"]
                .as_str()
                .expect("request path should be a string");
            assert_eq!(request_path, expected_path);
            assert!(request_path.starts_with("file:"));

            let response = serde_json::to_value(FsHelperResponse::Ok(
                FsHelperPayload::Canonicalize(FsCanonicalizeResponse { path }),
            ))?;
            assert_eq!(
                response,
                json!({
                    "status": "ok",
                    "payload": {
                        "operation": FS_CANONICALIZE_METHOD,
                        "response": {
                            "path": expected_path.as_str(),
                        },
                    },
                }),
            );
            let response_path = response["payload"]["response"]["path"]
                .as_str()
                .expect("canonicalize response path should be a string");
            assert_eq!(response_path, expected_path);
            assert!(response_path.starts_with("file:"));
        }

        Ok(())
    }

    // ---- 长命 fs/writeStream helper ----

    fn write_stream_params(path: &std::path::Path) -> FsWriteStreamParams {
        FsWriteStreamParams {
            handle_id: "w1".to_string(),
            path: PathUri::from_host_native_path(path).expect("path URI"),
            sandbox: None,
        }
    }

    fn chunk_event(seq: u64, bytes: &[u8], eof: bool) -> FsHelperWriteStreamEvent {
        FsHelperWriteStreamEvent::Chunk(FsWriteStreamChunkNotification {
            handle_id: "w1".to_string(),
            seq,
            chunk: bytes.to_vec().into(),
            eof,
        })
    }

    fn finish_event() -> FsHelperWriteStreamEvent {
        FsHelperWriteStreamEvent::Finish(FsWriteStreamDoneParams {
            handle_id: "w1".to_string(),
        })
    }

    /// 把事件帧序列编码为 helper stdin 的 NDJSON 字节流
    fn encode_stdin_frames(frames: &[FsHelperWriteStreamEvent]) -> Vec<u8> {
        let mut input = Vec::new();
        for frame in frames {
            input.extend(serde_json::to_vec(frame).expect("frame encode"));
            input.push(b'\n');
        }
        input
    }

    /// 跑一遍写流请求，返回（目标路径，stdout 帧序列）
    async fn run_write_stream(path: &std::path::Path, stdin_bytes: &[u8]) -> Vec<FsHelperResponse> {
        let mut stdin = tokio::io::BufReader::new(stdin_bytes).lines();
        let mut out = Vec::new();
        run_write_stream_request(write_stream_params(path), &mut stdin, &mut out)
            .await
            .expect("write stream request");
        out.split(|byte| *byte == b'\n')
            .filter(|line| !line.is_empty())
            .map(|line| serde_json::from_slice::<FsHelperResponse>(line).expect("response frame"))
            .collect()
    }

    fn write_stream_handshake() -> FsHelperResponse {
        FsHelperResponse::Ok(FsHelperPayload::WriteStream(FsWriteStreamResponse {
            handle_id: "w1".to_string(),
        }))
    }

    #[tokio::test]
    async fn write_stream_request_writes_handshake_and_final_done() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let path = temp_dir.path().join("out.bin");

        let input = encode_stdin_frames(&[
            chunk_event(0, b"hello ", false),
            chunk_event(1, b"wor", false),
            chunk_event(2, b"ld", true),
            finish_event(),
        ]);
        let frames = run_write_stream(&path, &input).await;

        assert_eq!(
            frames,
            vec![
                write_stream_handshake(),
                FsHelperResponse::Ok(FsHelperPayload::WriteStreamDone(
                    FsWriteStreamDoneResponse {
                        handle_id: "w1".to_string(),
                        total_bytes: 11,
                    },
                )),
            ]
        );
        assert_eq!(std::fs::read(&path).expect("read file"), b"hello world");
    }

    #[tokio::test]
    async fn write_stream_request_empty_stream_finishes_with_zero_bytes() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let path = temp_dir.path().join("empty.bin");

        let input = encode_stdin_frames(&[chunk_event(0, b"", true), finish_event()]);
        let frames = run_write_stream(&path, &input).await;

        assert_eq!(
            frames,
            vec![
                write_stream_handshake(),
                FsHelperResponse::Ok(FsHelperPayload::WriteStreamDone(
                    FsWriteStreamDoneResponse {
                        handle_id: "w1".to_string(),
                        total_bytes: 0,
                    },
                )),
            ]
        );
        assert_eq!(std::fs::read(&path).expect("read file"), b"");
    }

    #[tokio::test]
    async fn write_stream_request_reports_open_failure_as_handshake_error() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let path = temp_dir.path().join("missing-dir").join("out.bin");

        let frames = run_write_stream(&path, &encode_stdin_frames(&[])).await;

        assert_eq!(frames.len(), 1, "no stream frames after open failure");
        match &frames[0] {
            FsHelperResponse::Error(error) => assert_eq!(error.code, -32004),
            other => panic!("expected handshake error, got {other:?}"),
        }
        assert!(!path.exists());
    }

    #[tokio::test]
    async fn write_stream_request_reports_seq_violation_at_finish_and_removes_partial() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let path = temp_dir.path().join("out-of-order.bin");

        let input = encode_stdin_frames(&[
            chunk_event(0, b"aa", false),
            chunk_event(2, b"bb", true),
            finish_event(),
        ]);
        let frames = run_write_stream(&path, &input).await;

        assert_eq!(frames[0], write_stream_handshake());
        assert_eq!(frames.len(), 2);
        match &frames[1] {
            FsHelperResponse::Error(error) => {
                assert_eq!(error.code, -32600);
                assert!(error.message.contains("expected seq 1, got 2"));
            }
            other => panic!("expected finish error, got {other:?}"),
        }
        assert!(!path.exists(), "partial file should be removed");
    }

    #[tokio::test]
    async fn write_stream_request_finish_without_eof_fails_and_removes_partial() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let path = temp_dir.path().join("no-eof.bin");

        let input = encode_stdin_frames(&[chunk_event(0, b"half", false), finish_event()]);
        let frames = run_write_stream(&path, &input).await;

        assert_eq!(frames[0], write_stream_handshake());
        assert_eq!(frames.len(), 2);
        match &frames[1] {
            FsHelperResponse::Error(error) => {
                assert_eq!(error.code, -32600);
                assert!(error.message.contains("without an eof chunk"));
            }
            other => panic!("expected finish error, got {other:?}"),
        }
        assert!(!path.exists(), "partial file should be removed");
    }

    #[tokio::test]
    async fn write_stream_request_stdin_eof_aborts_and_removes_partial() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let path = temp_dir.path().join("aborted.bin");

        // executor 消失/中止：stdin EOF 而未见 Finish——删半截，无最终确认帧
        let input = encode_stdin_frames(&[chunk_event(0, b"half", false)]);
        let frames = run_write_stream(&path, &input).await;

        assert_eq!(frames, vec![write_stream_handshake()]);
        assert!(!path.exists(), "partial file should be removed");
    }

    #[tokio::test]
    async fn write_stream_request_reports_corrupt_frame_and_removes_partial() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let path = temp_dir.path().join("corrupt.bin");

        let mut input = encode_stdin_frames(&[chunk_event(0, b"half", false)]);
        input.extend(b"not a json frame\n");
        let frames = run_write_stream(&path, &input).await;

        assert_eq!(frames[0], write_stream_handshake());
        assert_eq!(frames.len(), 2);
        match &frames[1] {
            FsHelperResponse::Error(error) => {
                assert_eq!(error.code, -32600);
                assert!(error.message.contains("failed to decode"));
            }
            other => panic!("expected frame decode error, got {other:?}"),
        }
        assert!(!path.exists(), "partial file should be removed");
    }
}
