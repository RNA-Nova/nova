use std::collections::HashMap;
use std::io;
use std::sync::Arc;

use nova_executor_utils_path_uri::PathUri;
use tokio::io::AsyncWriteExt;
use tokio::sync::Mutex;
use tokio::sync::mpsc;
use tokio::sync::oneshot;
use tokio::task::JoinHandle;
use tokio_util::sync::CancellationToken;

use crate::protocol::FsWriteStreamChunkNotification;
use crate::protocol::FsWriteStreamDoneParams;

const MAX_OPEN_FILE_WRITES: usize = 128;
// 单个写入块的字节上限，与 readStream 的块上限（4MB）对齐
const MAX_WRITE_STREAM_CHUNK_BYTES: usize = 4 * 1024 * 1024;

/// 沙箱化写流的 executor 侧命令：RPC 层把线上 chunk 通知与 done 请求经此
/// 通道交给持有沙箱 helper 会话的后台任务（supervisor）逐条处理。
pub(crate) enum SandboxedWriteCommand {
    /// 数据块（与 fs/writeStream/chunk 通知同构，原样转发给 helper）
    Chunk(FsWriteStreamChunkNotification),
    /// 收尾确认（fs/writeStream/done）：supervisor 向 helper 收最终确认后经
    /// oneshot 应答；应答前 helper 回收与半截文件清理均已完成
    Finish {
        done: FsWriteStreamDoneParams,
        respond: oneshot::Sender<io::Result<u64>>,
    },
}

/// 沙箱化写流句柄：文件由长命沙箱 helper 子进程持有（executor 进程内没有
/// file 对象），句柄承载命令通道 + 中断令牌 + supervisor 任务句柄。
/// `abort`/`close_all` 取消令牌并等 supervisor 收尾（击杀 helper、删半截文件）。
pub(crate) struct FileWriteSandboxed {
    pub(crate) commands: mpsc::Sender<SandboxedWriteCommand>,
    pub(crate) cancel: CancellationToken,
    pub(crate) supervisor: JoinHandle<()>,
}

/// 写流句柄状态机。
///
/// 中断清理语义（对齐分片上传的常规语义）：未完成（未见 eof 块并经
/// `fs/writeStream/done` 确认）的流不产生可见文件——首个写入/协议错误、
/// 客户端中止（fs/close）与连接关闭一律删除半截文件，避免留下内容不明的半成品。
enum FileWriteState {
    Active(FileWriteActive),
    /// 沙箱化写流：数据经长命沙箱 helper 子进程落盘，executor 只转发命令；
    /// 中断清理（击杀 helper、删半截文件）由 supervisor 执行
    Sandboxed(FileWriteSandboxed),
    /// 首个错误后进入终态；错误保留到 done 请求时回报给客户端
    Failed {
        kind: io::ErrorKind,
        error: String,
    },
}

struct FileWriteActive {
    file: tokio::fs::File,
    /// 中断清理（删除半截文件）需要的目标路径
    path: PathUri,
    expected_seq: u64,
    total_bytes: u64,
    eof_seen: bool,
}

#[derive(Clone, Default)]
pub(crate) struct FileWriteHandleManager {
    handles: Arc<Mutex<HashMap<String, FileWriteState>>>,
}

impl FileWriteHandleManager {
    pub(crate) async fn open(
        &self,
        handle_id: String,
        path: PathUri,
        file: tokio::fs::File,
    ) -> io::Result<String> {
        let mut handles = self.handles.lock().await;
        check_handle_slot(&handles, &handle_id)?;
        handles.insert(
            handle_id.clone(),
            FileWriteState::Active(FileWriteActive {
                file,
                path,
                expected_seq: 0,
                total_bytes: 0,
                eof_seen: false,
            }),
        );
        Ok(handle_id)
    }

    /// 注册沙箱化写流句柄（fs/writeStream 的平台沙箱路径）：helper 会话与
    /// 中断清理由调用方装配的 supervisor 持有，这里只做槽位裁决与命令路由。
    pub(crate) async fn open_sandboxed(
        &self,
        handle_id: String,
        sandboxed: FileWriteSandboxed,
    ) -> io::Result<String> {
        let mut handles = self.handles.lock().await;
        check_handle_slot(&handles, &handle_id)?;
        handles.insert(handle_id.clone(), FileWriteState::Sandboxed(sandboxed));
        Ok(handle_id)
    }

    /// 顺序追加一个数据块。
    ///
    /// `fs/writeStream/chunk` 是通知（无回执），业务错误不向客户端返回，
    /// 只把句柄转入 Failed 终态（随即删除半截文件），由随后的
    /// `fs/writeStream/done` 请求把原始错误回报给客户端。
    pub(crate) async fn write_chunk(&self, handle_id: &str, seq: u64, bytes: Vec<u8>, eof: bool) {
        let mut handles = self.handles.lock().await;
        let Some(state) = handles.get_mut(handle_id) else {
            // 未知句柄：流可能已失败清理或从未建立；通知无回执，忽略即可
            tracing::warn!("ignoring write stream chunk for unknown handle `{handle_id}`");
            return;
        };
        match state {
            FileWriteState::Active(_) => {
                write_chunk_to_file(state, handle_id, seq, bytes, eof).await;
            }
            FileWriteState::Sandboxed(sandboxed) => {
                // 原样转发给沙箱 helper（seq/eof/块长校验在 helper 内由同一套
                // 句柄状态机执行）；通道已关 = supervisor 随 helper 死亡清理后
                // 退出，记终态错误留待 done 回报
                let forward = sandboxed
                    .commands
                    .send(SandboxedWriteCommand::Chunk(
                        FsWriteStreamChunkNotification {
                            handle_id: handle_id.to_string(),
                            seq,
                            chunk: bytes.into(),
                            eof,
                        },
                    ))
                    .await;
                if forward.is_err() {
                    *state = FileWriteState::Failed {
                        kind: io::ErrorKind::Other,
                        error: format!(
                            "file write stream `{handle_id}` failed: sandbox helper is gone"
                        ),
                    };
                }
            }
            FileWriteState::Failed { .. } => {
                // Failed 终态：静默忽略后续块，等 done 回报首个错误
            }
        }
    }

    /// 收尾确认（`fs/writeStream/done`）：流须已见 eof 块。
    /// 成功后移除句柄并返回总落盘字节数。
    pub(crate) async fn finish(&self, handle_id: &str) -> io::Result<u64> {
        let state = self.handles.lock().await.remove(handle_id);
        let Some(state) = state else {
            return Err(unknown_handle_error(handle_id));
        };
        match state {
            FileWriteState::Failed { kind, error } => Err(io::Error::new(kind, error)),
            FileWriteState::Active(active) => {
                if !active.eof_seen {
                    drop(active.file);
                    remove_partial_file(&active.path).await;
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidInput,
                        format!("file write stream `{handle_id}` finished without an eof chunk"),
                    ));
                }
                // tokio::fs::File 无用户态缓冲，drop 即关闭；不做 fsync，
                // 与 fs/writeFile（tokio::fs::write）的落盘语义对齐。
                drop(active.file);
                Ok(active.total_bytes)
            }
            FileWriteState::Sandboxed(sandboxed) => {
                // 转发 done 请求并等 supervisor 应答（应答时 helper 回收与
                // 半截清理均已完成），随后等 supervisor 完全退出
                let FileWriteSandboxed {
                    commands,
                    cancel: _,
                    supervisor,
                } = sandboxed;
                let (respond_tx, respond_rx) = oneshot::channel();
                let result = match commands
                    .send(SandboxedWriteCommand::Finish {
                        done: FsWriteStreamDoneParams {
                            handle_id: handle_id.to_string(),
                        },
                        respond: respond_tx,
                    })
                    .await
                {
                    Ok(()) => respond_rx.await.unwrap_or_else(|_| {
                        Err(io::Error::other(format!(
                            "file write stream `{handle_id}` supervisor stopped unexpectedly"
                        )))
                    }),
                    Err(_) => Err(io::Error::other(format!(
                        "file write stream `{handle_id}` failed: sandbox helper is gone"
                    ))),
                };
                let _ = supervisor.await;
                result
            }
        }
    }

    /// 中止写流（`fs/close`）：删除未完成流的半截文件。
    pub(crate) async fn abort(&self, handle_id: &str) {
        let state = self.handles.lock().await.remove(handle_id);
        match state {
            Some(FileWriteState::Active(active)) => {
                drop(active.file);
                remove_partial_file(&active.path).await;
            }
            Some(FileWriteState::Sandboxed(sandboxed)) => {
                // 中断令牌通知 supervisor 击杀 helper 并删除半截文件；
                // 等其收尾完成（与本地 abort 的同步清理语义一致）
                sandboxed.cancel.cancel();
                drop(sandboxed.commands);
                let _ = sandboxed.supervisor.await;
            }
            _ => {}
        }
    }

    /// 连接关闭清理：删除全部未完成流的半截文件（Failed 终态在失败时已删）。
    pub(crate) async fn close_all(&self) {
        let states = {
            let mut handles = self.handles.lock().await;
            handles.drain().map(|(_, state)| state).collect::<Vec<_>>()
        };
        for state in states {
            match state {
                FileWriteState::Active(active) => {
                    drop(active.file);
                    remove_partial_file(&active.path).await;
                }
                FileWriteState::Sandboxed(sandboxed) => {
                    sandboxed.cancel.cancel();
                    drop(sandboxed.commands);
                    let _ = sandboxed.supervisor.await;
                }
                FileWriteState::Failed { .. } => {}
            }
        }
    }

    #[cfg(test)]
    pub(crate) async fn open_handle_count(&self) -> usize {
        self.handles.lock().await.len()
    }
}

fn check_handle_slot(handles: &HashMap<String, FileWriteState>, handle_id: &str) -> io::Result<()> {
    if handles.contains_key(handle_id) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("file write handle `{handle_id}` already exists"),
        ));
    }
    if handles.len() >= MAX_OPEN_FILE_WRITES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("at most {MAX_OPEN_FILE_WRITES} file writes may be open per connection"),
        ));
    }
    Ok(())
}

/// 本地写流（Active）的单块写入：协议校验 + 落盘 + 失败转终态。
async fn write_chunk_to_file(
    state: &mut FileWriteState,
    handle_id: &str,
    seq: u64,
    bytes: Vec<u8>,
    eof: bool,
) {
    let FileWriteState::Active(active) = &mut *state else {
        return;
    };
    let violation = if active.eof_seen {
        Some(format!(
            "file write stream `{handle_id}` received chunk after eof"
        ))
    } else if seq != active.expected_seq {
        Some(format!(
            "file write stream `{handle_id}` expected seq {}, got {seq}",
            active.expected_seq
        ))
    } else if bytes.len() > MAX_WRITE_STREAM_CHUNK_BYTES {
        Some(format!(
            "file write stream chunk must not exceed {MAX_WRITE_STREAM_CHUNK_BYTES} bytes"
        ))
    } else {
        None
    };
    if let Some(error) = violation {
        fail_stream(state, io::ErrorKind::InvalidInput, error).await;
        return;
    }

    if let Err(err) = active.file.write_all(&bytes).await {
        fail_stream(
            state,
            err.kind(),
            format!("file write stream `{handle_id}` write failed: {err}"),
        )
        .await;
        return;
    }
    active.total_bytes = active.total_bytes.saturating_add(bytes.len() as u64);
    active.expected_seq += 1;
    active.eof_seen = eof;
}

async fn fail_stream(state: &mut FileWriteState, kind: io::ErrorKind, error: String) {
    // 先替换出活动状态以关闭文件并删除半截文件，再进入 Failed 终态
    let FileWriteState::Active(active) =
        std::mem::replace(state, FileWriteState::Failed { kind, error })
    else {
        return;
    };
    drop(active.file);
    remove_partial_file(&active.path).await;
}

/// 删除未完成流的半截文件（尽力而为：文件已不存在或路径不可解析仅告警）。
async fn remove_partial_file(path: &PathUri) {
    let path = match path.to_abs_path() {
        Ok(path) => path,
        Err(err) => {
            tracing::warn!("failed to resolve partial write stream file `{path}`: {err}");
            return;
        }
    };
    match tokio::fs::remove_file(path.as_path()).await {
        Ok(()) => {}
        Err(err) if err.kind() == io::ErrorKind::NotFound => {}
        Err(err) => {
            tracing::warn!(
                "failed to remove partial write stream file `{}`: {err}",
                path.as_path().display()
            );
        }
    }
}

fn unknown_handle_error(handle_id: &str) -> io::Error {
    io::Error::new(
        io::ErrorKind::NotFound,
        format!("unknown file write handle `{handle_id}`"),
    )
}

#[cfg(test)]
mod tests {
    use nova_executor_utils_path_uri::PathUri;
    use pretty_assertions::assert_eq;

    use super::*;

    async fn open_test_stream(
        writes: &FileWriteHandleManager,
        temp_dir: &tempfile::TempDir,
        handle_id: &str,
        file_name: &str,
    ) -> std::path::PathBuf {
        let path = temp_dir.path().join(file_name);
        let uri = PathUri::from_host_native_path(&path).expect("path URI");
        let file = tokio::fs::File::create(&path).await.expect("create file");
        writes
            .open(handle_id.to_string(), uri, file)
            .await
            .expect("open write stream");
        path
    }

    #[tokio::test]
    async fn write_chunks_aggregate_in_order_and_finish_reports_total_bytes() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let writes = FileWriteHandleManager::default();
        let path = open_test_stream(&writes, &temp_dir, "w1", "out.bin").await;

        writes.write_chunk("w1", 0, b"hello ".to_vec(), false).await;
        writes.write_chunk("w1", 1, b"world".to_vec(), false).await;
        writes.write_chunk("w1", 2, Vec::new(), true).await;

        let total = writes.finish("w1").await.expect("finish");
        assert_eq!(total, 11);
        assert_eq!(std::fs::read(&path).expect("read file"), b"hello world");
    }

    #[tokio::test]
    async fn empty_stream_finishes_with_zero_bytes() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let writes = FileWriteHandleManager::default();
        let path = open_test_stream(&writes, &temp_dir, "w1", "empty.bin").await;

        writes.write_chunk("w1", 0, Vec::new(), true).await;

        assert_eq!(writes.finish("w1").await.expect("finish"), 0);
        assert_eq!(std::fs::read(&path).expect("read file"), b"");
    }

    #[tokio::test]
    async fn finish_without_eof_fails_and_removes_partial_file() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let writes = FileWriteHandleManager::default();
        let path = open_test_stream(&writes, &temp_dir, "w1", "partial.bin").await;

        writes.write_chunk("w1", 0, b"half".to_vec(), false).await;

        let err = writes.finish("w1").await.expect_err("finish should fail");
        assert_eq!(err.kind(), io::ErrorKind::InvalidInput);
        assert!(err.to_string().contains("without an eof chunk"));
        assert!(!path.exists(), "partial file should be removed");
    }

    #[tokio::test]
    async fn out_of_order_seq_fails_stream_and_done_reports_error() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let writes = FileWriteHandleManager::default();
        let path = open_test_stream(&writes, &temp_dir, "w1", "out-of-order.bin").await;

        writes.write_chunk("w1", 0, b"aa".to_vec(), false).await;
        writes.write_chunk("w1", 2, b"bb".to_vec(), true).await;
        // 流已失败：半截文件随即删除，后续块静默忽略
        assert!(!path.exists(), "partial file should be removed on failure");
        writes.write_chunk("w1", 3, b"cc".to_vec(), true).await;

        let err = writes.finish("w1").await.expect_err("done should fail");
        assert_eq!(err.kind(), io::ErrorKind::InvalidInput);
        assert!(err.to_string().contains("expected seq 1, got 2"));
        assert!(!path.exists());
    }

    #[tokio::test]
    async fn chunk_after_eof_fails_stream() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let writes = FileWriteHandleManager::default();
        let path = open_test_stream(&writes, &temp_dir, "w1", "after-eof.bin").await;

        writes.write_chunk("w1", 0, b"aa".to_vec(), true).await;
        writes.write_chunk("w1", 1, b"bb".to_vec(), true).await;

        let err = writes.finish("w1").await.expect_err("done should fail");
        assert_eq!(err.kind(), io::ErrorKind::InvalidInput);
        assert!(err.to_string().contains("after eof"));
        assert!(!path.exists());
    }

    #[tokio::test]
    async fn oversized_chunk_fails_stream() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let writes = FileWriteHandleManager::default();
        let path = open_test_stream(&writes, &temp_dir, "w1", "oversized.bin").await;

        writes
            .write_chunk("w1", 0, vec![0u8; MAX_WRITE_STREAM_CHUNK_BYTES + 1], true)
            .await;

        let err = writes.finish("w1").await.expect_err("done should fail");
        assert_eq!(err.kind(), io::ErrorKind::InvalidInput);
        assert!(!path.exists());
    }

    #[tokio::test]
    async fn abort_removes_partial_file() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let writes = FileWriteHandleManager::default();
        let path = open_test_stream(&writes, &temp_dir, "w1", "aborted.bin").await;

        writes.write_chunk("w1", 0, b"half".to_vec(), false).await;
        writes.abort("w1").await;

        assert!(!path.exists(), "partial file should be removed on abort");
        // 中止后 done 报未知句柄；重复 abort 为空操作
        let err = writes.finish("w1").await.expect_err("done should fail");
        assert_eq!(err.kind(), io::ErrorKind::NotFound);
        writes.abort("w1").await;
    }

    #[tokio::test]
    async fn close_all_removes_all_partial_files() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let writes = FileWriteHandleManager::default();
        let first = open_test_stream(&writes, &temp_dir, "w1", "first.bin").await;
        let second = open_test_stream(&writes, &temp_dir, "w2", "second.bin").await;

        writes.write_chunk("w1", 0, b"aa".to_vec(), false).await;
        writes.write_chunk("w2", 0, b"bb".to_vec(), false).await;
        writes.close_all().await;

        assert!(!first.exists());
        assert!(!second.exists());
    }

    #[tokio::test]
    async fn duplicate_handle_id_is_rejected() {
        let temp_dir = tempfile::tempdir().expect("tempdir");
        let writes = FileWriteHandleManager::default();
        open_test_stream(&writes, &temp_dir, "w1", "first.bin").await;

        let uri =
            PathUri::from_host_native_path(temp_dir.path().join("second.bin")).expect("path URI");
        let file = tokio::fs::File::create(temp_dir.path().join("second.bin"))
            .await
            .expect("create file");
        let err = writes
            .open("w1".to_string(), uri, file)
            .await
            .expect_err("duplicate handle should fail");
        assert_eq!(err.kind(), io::ErrorKind::InvalidInput);
    }

    // ---- 沙箱化写流句柄（supervisor 用 mock 任务代替）----

    /// 注册一个 Sandboxed 句柄并返回其令牌；`supervisor` 为调用方给定的 mock 任务
    async fn open_mock_sandboxed(
        writes: &FileWriteHandleManager,
        handle_id: &str,
        commands: mpsc::Sender<SandboxedWriteCommand>,
        supervisor: JoinHandle<()>,
    ) -> CancellationToken {
        let cancel = CancellationToken::new();
        writes
            .open_sandboxed(
                handle_id.to_string(),
                FileWriteSandboxed {
                    commands,
                    cancel: cancel.clone(),
                    supervisor,
                },
            )
            .await
            .expect("open sandboxed write stream");
        cancel
    }

    #[tokio::test]
    async fn sandboxed_stream_forwards_chunks_and_finish_roundtrips() {
        let writes = FileWriteHandleManager::default();
        let (commands_tx, mut commands_rx) = mpsc::channel(8);
        // mock supervisor：累加 chunk 字节数，finish 应答总和
        let supervisor = tokio::spawn(async move {
            let mut total = 0u64;
            while let Some(command) = commands_rx.recv().await {
                match command {
                    SandboxedWriteCommand::Chunk(chunk) => {
                        total += chunk.chunk.into_inner().len() as u64;
                    }
                    SandboxedWriteCommand::Finish { respond, .. } => {
                        let _ = respond.send(Ok(total));
                        return;
                    }
                }
            }
        });
        open_mock_sandboxed(&writes, "w1", commands_tx, supervisor).await;

        writes.write_chunk("w1", 0, b"ab".to_vec(), false).await;
        writes.write_chunk("w1", 1, b"c".to_vec(), true).await;

        assert_eq!(writes.finish("w1").await.expect("finish"), 3);
    }

    #[tokio::test]
    async fn sandboxed_stream_finish_propagates_supervisor_error() {
        let writes = FileWriteHandleManager::default();
        let (commands_tx, mut commands_rx) = mpsc::channel(8);
        let supervisor = tokio::spawn(async move {
            while let Some(command) = commands_rx.recv().await {
                if let SandboxedWriteCommand::Finish { respond, .. } = command {
                    let _ = respond.send(Err(io::Error::new(
                        io::ErrorKind::InvalidInput,
                        "file write stream `w1` expected seq 1, got 2",
                    )));
                    return;
                }
            }
        });
        open_mock_sandboxed(&writes, "w1", commands_tx, supervisor).await;

        writes.write_chunk("w1", 0, b"aa".to_vec(), false).await;
        let err = writes.finish("w1").await.expect_err("finish should fail");
        assert_eq!(err.kind(), io::ErrorKind::InvalidInput);
        assert!(err.to_string().contains("expected seq 1, got 2"));
    }

    #[tokio::test]
    async fn sandboxed_stream_abort_cancels_and_reaps_supervisor() {
        let writes = FileWriteHandleManager::default();
        let (commands_tx, mut commands_rx) = mpsc::channel(8);
        let cancel = CancellationToken::new();
        let cancel_in_task = cancel.clone();
        // mock supervisor：排空通道直到令牌取消（对齐真实 supervisor 的中断语义）
        let supervisor = tokio::spawn(async move {
            loop {
                tokio::select! {
                    biased;
                    _ = cancel_in_task.cancelled() => break,
                    command = commands_rx.recv() => {
                        if command.is_none() {
                            break;
                        }
                    }
                }
            }
        });
        let registered = writes
            .open_sandboxed(
                "w1".to_string(),
                FileWriteSandboxed {
                    commands: commands_tx,
                    cancel: cancel.clone(),
                    supervisor,
                },
            )
            .await
            .expect("open sandboxed write stream");

        // abort 应取消令牌并等 supervisor 退出；随后 done 报未知句柄
        writes.abort(&registered).await;
        assert!(cancel.is_cancelled());
        let err = writes
            .finish("w1")
            .await
            .expect_err("done after abort should fail");
        assert_eq!(err.kind(), io::ErrorKind::NotFound);
    }

    #[tokio::test]
    async fn sandboxed_stream_chunk_after_supervisor_exit_marks_failed() {
        let writes = FileWriteHandleManager::default();
        let (commands_tx, commands_rx) = mpsc::channel(8);
        let (dropped_tx, dropped_rx) = oneshot::channel();
        // mock supervisor 立即关闭通道（模拟 helper 死亡清理后退出）
        let supervisor = tokio::spawn(async move {
            drop(commands_rx);
            let _ = dropped_tx.send(());
        });
        open_mock_sandboxed(&writes, "w1", commands_tx, supervisor).await;
        dropped_rx
            .await
            .expect("supervisor closed the command channel");

        // 通道已关：chunk 转发失败转 Failed 终态，done 回报错误
        writes.write_chunk("w1", 0, b"ab".to_vec(), false).await;
        let err = writes.finish("w1").await.expect_err("finish should fail");
        assert_eq!(err.kind(), io::ErrorKind::Other);
        assert!(err.to_string().contains("sandbox helper is gone"));
    }

    #[tokio::test]
    async fn sandboxed_stream_close_all_cancels_all_supervisors() {
        let writes = FileWriteHandleManager::default();
        let mut cancels = Vec::new();
        for handle_id in ["w1", "w2"] {
            let (commands_tx, mut commands_rx) = mpsc::channel(8);
            let cancel = CancellationToken::new();
            let cancel_in_task = cancel.clone();
            // mock supervisor：排空通道直到令牌取消（对齐真实 supervisor 的中断语义）
            let supervisor = tokio::spawn(async move {
                loop {
                    tokio::select! {
                        biased;
                        _ = cancel_in_task.cancelled() => break,
                        command = commands_rx.recv() => {
                            if command.is_none() {
                                break;
                            }
                        }
                    }
                }
            });
            writes
                .open_sandboxed(
                    handle_id.to_string(),
                    FileWriteSandboxed {
                        commands: commands_tx,
                        cancel: cancel.clone(),
                        supervisor,
                    },
                )
                .await
                .expect("open sandboxed write stream");
            cancels.push(cancel);
        }

        writes.close_all().await;
        for cancel in &cancels {
            assert!(cancel.is_cancelled());
        }
    }

    #[tokio::test]
    async fn sandboxed_duplicate_handle_id_is_rejected() {
        let writes = FileWriteHandleManager::default();
        let (commands_tx, mut commands_rx) = mpsc::channel(8);
        let supervisor = tokio::spawn(async move { while commands_rx.recv().await.is_some() {} });
        open_mock_sandboxed(&writes, "w1", commands_tx, supervisor).await;

        let (dup_tx, mut dup_rx) = mpsc::channel(8);
        let dup_supervisor = tokio::spawn(async move { while dup_rx.recv().await.is_some() {} });
        let err = writes
            .open_sandboxed(
                "w1".to_string(),
                FileWriteSandboxed {
                    commands: dup_tx,
                    cancel: CancellationToken::new(),
                    supervisor: dup_supervisor,
                },
            )
            .await
            .expect_err("duplicate handle should fail");
        assert_eq!(err.kind(), io::ErrorKind::InvalidInput);
    }
}
