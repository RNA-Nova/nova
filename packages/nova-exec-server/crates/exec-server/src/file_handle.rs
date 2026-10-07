//! 连接级文件句柄表：定位读/写均有界（对位 codex d25c114d49+c39bfa4c8f
//! `file_handle.rs`）。信号量槽位同时约束已注册句柄与在飞行打开；
//! 句柄表用同步锁，锁不跨 await。
//!
//! 沙箱化 fs/open|readStream 也由 executor 进程自持句柄（一次性 helper 开门后
//! 把 fd/handle 传回，见 sandboxed_file_open），因此句柄统一为普通 file 对象。
//!
//! fs/writeStream 与读流完全镜像（nova 自有通道，v1.12 起收编句柄族）：
//! open 注册即 spawn 写任务（对读任务 `stream_file_blocks`）——chunk 通知只投进
//! 写任务 channel，seq/eof 校验与定位写（经与 writeBlock 共享的核心）全部在
//! 任务栈内完成；done 请求经一次性应答通道向任务交账。句柄条目零游标状态。

use std::collections::HashMap;
use std::collections::hash_map::Entry;
use std::fs::File;
use std::future::Future;
use std::io;
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::MutexGuard;

use nova_exec_server_file_system::FILE_READ_CHUNK_SIZE;
use nova_exec_server_file_system::FILE_WRITE_CHUNK_SIZE;
use tokio::sync::OwnedSemaphorePermit;
use tokio::sync::Semaphore;

const MAX_OPEN_FILES: usize = 128;

// nova 自有件（fs/readStream 服务端循环用，上游无对应）：默认/最大流式读块尺寸
pub(crate) const DEFAULT_READ_STREAM_BLOCK_SIZE: usize = 256 * 1024; // 256KB
pub(crate) const MAX_READ_STREAM_BLOCK_SIZE: usize = 4 * 1024 * 1024; // 4MB
/// fs/writeStream 单块字节上限（nova 自有通道）：与 readStream 的块上限对齐
/// （v1.12 起随写流收编进句柄族——自 file_write.rs 迁入）。
pub(crate) const MAX_WRITE_STREAM_CHUNK_BYTES: usize = 4 * 1024 * 1024; // 4MB

#[derive(Debug, Eq, PartialEq)]
pub(crate) struct FileReadBlock {
    pub(crate) bytes: Vec<u8>,
    pub(crate) eof: bool,
}

/// fs/writeStream 写任务的输入消息（nova 自有通道）：chunk 与线上通知同构
/// （fire-and-forget）；Done 携带一次性应答通道（done 请求的交账出口）。
enum WriteStreamInput {
    Chunk {
        seq: u64,
        bytes: Vec<u8>,
        eof: bool,
    },
    Done {
        respond: tokio::sync::oneshot::Sender<Result<u64, (io::ErrorKind, String)>>,
    },
}

/// fs/writeStream 的句柄载荷（nova 自有通道，v1.12 起与读流完全镜像）：
/// 条目只承载写任务的 channel 发送端——全部流状态（expected_seq/cursor/
/// eof_seen/failed）住进写任务栈，不在句柄表里挂账。
struct WriteStreamHandle {
    tx: tokio::sync::mpsc::UnboundedSender<WriteStreamInput>,
}

#[derive(Clone)]
pub(crate) struct FileHandleManager {
    handles: Arc<Mutex<HashMap<String, FileHandleEntry>>>,
    slots: Arc<Semaphore>,
}

impl Default for FileHandleManager {
    fn default() -> Self {
        Self {
            handles: Arc::default(),
            slots: Arc::new(Semaphore::new(MAX_OPEN_FILES)),
        }
    }
}

impl FileHandleManager {
    /// 先占槽再 await 打开 future——在飞行打开也计入 128 槽上限（对位 codex
    /// d25c114d49）；打开完成后复查重复 ID，并发同名时保留先到句柄。
    pub(crate) async fn open(
        &self,
        handle_id: String,
        open_file: impl Future<Output = io::Result<tokio::fs::File>>,
    ) -> io::Result<String> {
        self.open_entry(handle_id, open_file, None).await
    }

    /// fs/writeStream 开门注册（nova 自有通道）：与 `open` 同一套容量/ID 校验；
    /// 注册即 spawn 写任务（与读任务 `stream_file_blocks` 完全镜像——状态全部
    /// 住进任务栈），条目只承载 channel 发送端。`offset` = 续传起点（写任务
    /// 游标初值；None = 创建/截断语义）。
    pub(crate) async fn open_write_stream(
        &self,
        handle_id: String,
        open_file: impl Future<Output = io::Result<tokio::fs::File>>,
        offset: Option<u64>,
    ) -> io::Result<String> {
        self.open_entry(handle_id, open_file, Some(offset.unwrap_or(0)))
            .await
    }

    async fn open_entry(
        &self,
        handle_id: String,
        open_file: impl Future<Output = io::Result<tokio::fs::File>>,
        write_stream_offset: Option<u64>,
    ) -> io::Result<String> {
        if self.lock_handles().contains_key(&handle_id) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                format!("file handle `{handle_id}` already exists"),
            ));
        }
        let permit = Arc::clone(&self.slots).try_acquire_owned().map_err(|_| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                format!("at most {MAX_OPEN_FILES} file handles may be open per connection"),
            )
        })?;
        let file = Arc::new(open_file.await?.into_std().await);
        let write_stream_channel = write_stream_offset
            .map(|_| tokio::sync::mpsc::unbounded_channel());
        let write_stream = write_stream_channel
            .as_ref()
            .map(|(tx, _rx)| WriteStreamHandle { tx: tx.clone() });
        let mut handles = self.lock_handles();
        let Entry::Vacant(entry) = handles.entry(handle_id.clone()) else {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                format!("file handle `{handle_id}` already exists"),
            ));
        };
        entry.insert(FileHandleEntry {
            file: Arc::clone(&file),
            write_stream,
            _permit: permit,
        });
        drop(handles);
        // 写任务在条目落表后才启动（与读任务的 spawn 时机一致）——重复 ID 被
        // 拒绝时 channel 随局部变量 drop，不会误摘既有句柄。
        if let Some((_tx, rx)) = write_stream_channel {
            tokio::spawn(run_write_stream_task(
                self.clone(),
                handle_id.clone(),
                file,
                rx,
                write_stream_offset.expect("write stream offset is set"),
            ));
        }
        Ok(handle_id)
    }

    pub(crate) async fn read_block(
        &self,
        handle_id: &str,
        offset: u64,
        len: usize,
    ) -> io::Result<FileReadBlock> {
        validate_read_block_len(len)?;
        let file = self.get(handle_id)?;
        let result =
            match tokio::task::spawn_blocking(move || read_block_at(&file, offset, len)).await {
                Ok(result) => result,
                Err(error) => Err(io::Error::other(format!(
                    "file read task stopped unexpectedly: {error}"
                ))),
            };
        if result.is_err() {
            self.close(handle_id);
        }
        result
    }

    /// fs/writeBlock 执行体（对位 codex c39bfa4c8f）：非空块 ≤FILE_WRITE_CHUNK_SIZE，
    /// 写区间不得超 i64 上限（Windows 把负 offset 当哨兵值）；定位写委托给
    /// 与 fs/writeStream 共享的核心 [`write_block_at`]（写失败不关句柄——
    /// 与读路径的 read 失败关句柄不同——上游如此）。
    pub(crate) async fn write_block(
        &self,
        handle_id: &str,
        offset: u64,
        bytes: Vec<u8>,
    ) -> io::Result<()> {
        validate_write_block_len(bytes.len())?;
        checked_write_end(offset, bytes.len())?;
        let file = self.get(handle_id)?;
        write_block_at(file, offset, bytes).await
    }

    /// fs/writeStream/chunk 执行体（nova 自有通道，与读流的通知推送完全镜像——
    /// fire-and-forget）：只把块投进写任务的 channel，不碰任何流状态；
    /// seq/eof/块长校验与定位写全部在写任务栈内完成（状态翻转语义同前：
    /// 失败/中止不再删半成品——文件留在盘上，为断点续传让路）。
    pub(crate) async fn write_stream_chunk(
        &self,
        handle_id: &str,
        seq: u64,
        bytes: Vec<u8>,
        eof: bool,
    ) {
        let sender = {
            let handles = self.lock_handles();
            match handles.get(handle_id) {
                Some(entry) => entry.write_stream.as_ref().map(|stream| stream.tx.clone()),
                None => {
                    // 未知句柄：流可能已完成或从未建立；通知无回执，忽略即可
                    tracing::warn!("ignoring write stream chunk for unknown handle `{handle_id}`");
                    return;
                }
            }
        };
        match sender {
            Some(tx) => {
                // 任务已结束（理论上仅 done 之后——那时条目已摘除走不到这里；
                // 防御性路径）静默即可，与通知无回执的姿态一致
                let _ = tx.send(WriteStreamInput::Chunk { seq, bytes, eof });
            }
            None => {
                tracing::warn!(
                    "ignoring write stream chunk for non-stream handle `{handle_id}`"
                );
            }
        }
    }

    /// fs/writeStream/done 执行体：把 Done 投进写任务的 channel 并等交账
    /// （与读流的 done 收尾镜像）。流须已见 eof 块；失败终态回报首个错误。
    /// 成功返回全量字节数（offset 起点 + 本次流式字节数——游标终点，客户端
    /// 对账用），写任务随后摘句柄退出；无论成败文件均留在盘上。
    pub(crate) async fn finish_write_stream(&self, handle_id: &str) -> io::Result<u64> {
        let sender = {
            let handles = self.lock_handles();
            match handles.get(handle_id) {
                Some(entry) => match entry.write_stream.as_ref() {
                    Some(stream) => stream.tx.clone(),
                    None => {
                        return Err(io::Error::new(
                            io::ErrorKind::InvalidInput,
                            format!("file handle `{handle_id}` is not a write stream"),
                        ));
                    }
                },
                None => return Err(unknown_handle_error(handle_id)),
            }
        };
        let (respond_tx, respond_rx) = tokio::sync::oneshot::channel();
        if sender
            .send(WriteStreamInput::Done { respond: respond_tx })
            .is_err()
        {
            return Err(io::Error::other(format!(
                "file write stream `{handle_id}` task stopped unexpectedly"
            )));
        }
        let result = respond_rx.await.map_err(|_| {
            io::Error::other(format!(
                "file write stream `{handle_id}` task stopped unexpectedly"
            ))
        })?;
        result.map_err(|(kind, error)| io::Error::new(kind, error))
    }

    fn get(&self, handle_id: &str) -> io::Result<Arc<File>> {
        self.lock_handles()
            .get(handle_id)
            .map(|entry| Arc::clone(&entry.file))
            .ok_or_else(|| unknown_handle_error(handle_id))
    }

    pub(crate) fn close(&self, handle_id: &str) {
        self.lock_handles().remove(handle_id);
    }

    pub(crate) fn close_all(&self) {
        self.lock_handles().clear();
    }

    fn lock_handles(&self) -> MutexGuard<'_, HashMap<String, FileHandleEntry>> {
        self.handles
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    /// nova 自有测试观察口（readStream/writeStream 的 handler 测试断言句柄回收）。
    #[cfg(test)]
    pub(crate) fn open_handle_count(&self) -> usize {
        self.lock_handles().len()
    }
}

struct FileHandleEntry {
    file: Arc<File>,
    /// fs/writeStream 的写任务 channel 发送端（nova 自有通道；None = 非流句柄）
    write_stream: Option<WriteStreamHandle>,
    // Closing an entry releases capacity even if a read or write still holds the file.
    _permit: OwnedSemaphorePermit,
}

/// fs/readStream 的共享流式读循环（nova 自有件，上游无对应）：executor 自读路径与
/// 沙箱开门（fd 传递）路径共用同一份 offset/len/eof 语义，保证两种执行体线上行为一致。
///
/// 逐块读取已注册句柄并经 `emit_chunk(seq, bytes, eof)` 推出；`emit_chunk`
/// 返回 false 表示对端已消失，循环静默停止（不再读取，error 保持 None）。
/// 返回 `(total_bytes, error)`，与 fs/readStream/done 通知的载荷一致。
pub(crate) async fn stream_file_blocks<F>(
    file_handles: &FileHandleManager,
    handle_id: &str,
    offset: u64,
    len: Option<u64>,
    block_size: usize,
    mut emit_chunk: F,
) -> (u64, Option<String>)
where
    F: AsyncFnMut(u64, Vec<u8>, bool) -> bool,
{
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

        match file_handles
            .read_block(handle_id, current_offset, read_len)
            .await
        {
            Ok(block) => {
                let bytes_len = block.bytes.len() as u64;
                let eof = block.eof || remaining.is_some_and(|r| r <= bytes_len);
                total_bytes = total_bytes.saturating_add(bytes_len);
                current_offset = current_offset.saturating_add(bytes_len);

                if !emit_chunk(seq, block.bytes, eof).await {
                    break;
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

    (total_bytes, error)
}

/// fs/writeStream 的写任务（nova 自有通道，与读任务 `stream_file_blocks`
/// 完全镜像）：全部流状态住进任务栈——expected_seq/cursor/eof_seen/failed
/// （游标初值即续传 offset；done 的 totalBytes 全量语义 = 游标终点）。
///
/// 循环：从 channel 收块 → 协议校验（seq 严格序/eof 序/块长）→ 经
/// `write_block_at` 定位落盘；失败记为终态（latch，由随后的 Done 回报首个
/// 错误，与收编前语义逐字节一致）。Done 到来即交账并退出（句柄摘除）；
/// channel 对端全掉（close/断连摘条目 → tx drop → recv=None）则自然结束——
/// 无论成败文件留在盘上（v1.12 语义翻转）。
async fn run_write_stream_task(
    manager: FileHandleManager,
    handle_id: String,
    file: Arc<File>,
    mut rx: tokio::sync::mpsc::UnboundedReceiver<WriteStreamInput>,
    offset: u64,
) {
    let mut expected_seq = 0_u64;
    let mut cursor = offset;
    let mut eof_seen = false;
    let mut failed: Option<(io::ErrorKind, String)> = None;

    while let Some(input) = rx.recv().await {
        match input {
            WriteStreamInput::Chunk { seq, bytes, eof } => {
                if failed.is_some() {
                    // 失败终态：静默忽略后续块，等 Done 回报首个错误
                    continue;
                }
                let violation = if eof_seen {
                    Some(format!(
                        "file write stream `{handle_id}` received chunk after eof"
                    ))
                } else if seq != expected_seq {
                    Some(format!(
                        "file write stream `{handle_id}` expected seq {expected_seq}, got {seq}"
                    ))
                } else if bytes.len() > MAX_WRITE_STREAM_CHUNK_BYTES {
                    Some(format!(
                        "file write stream chunk must not exceed {MAX_WRITE_STREAM_CHUNK_BYTES} bytes"
                    ))
                } else {
                    None
                };
                if let Some(error) = violation {
                    failed = Some((io::ErrorKind::InvalidInput, error));
                    continue;
                }
                if !bytes.is_empty() {
                    let bytes_len = bytes.len() as u64;
                    if let Err(err) = write_block_at(Arc::clone(&file), cursor, bytes).await {
                        failed = Some((
                            err.kind(),
                            format!("file write stream `{handle_id}` write failed: {err}"),
                        ));
                        continue;
                    }
                    cursor += bytes_len;
                }
                // 空块（eof 哨兵）只携带标志位，不落盘
                expected_seq += 1;
                eof_seen = eof;
            }
            WriteStreamInput::Done { respond } => {
                let result = match (failed.take(), eof_seen) {
                    (Some(failure), _) => Err(failure),
                    (None, false) => Err((
                        io::ErrorKind::InvalidInput,
                        format!("file write stream `{handle_id}` finished without an eof chunk"),
                    )),
                    // 定位写经 spawn_blocking 内同步 write 落定，交账即全部落盘
                    (None, true) => Ok(cursor),
                };
                let _ = respond.send(result);
                break;
            }
        }
    }
    manager.close(&handle_id);
}

fn read_block_at(file: &File, offset: u64, len: usize) -> io::Result<FileReadBlock> {
    let mut bytes = vec![0; len];
    let mut bytes_read = 0;
    while bytes_read < len {
        let read_offset = offset.checked_add(bytes_read as u64).ok_or_else(|| {
            io::Error::new(io::ErrorKind::InvalidInput, "file read offset overflowed")
        })?;
        match read_file_at(file, &mut bytes[bytes_read..], read_offset) {
            Ok(0) => break,
            Ok(read) => bytes_read += read,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
            Err(error) => return Err(error),
        }
    }
    bytes.truncate(bytes_read);
    Ok(FileReadBlock {
        eof: bytes_read < len,
        bytes,
    })
}

#[cfg(unix)]
fn read_file_at(file: &File, bytes: &mut [u8], offset: u64) -> io::Result<usize> {
    std::os::unix::fs::FileExt::read_at(file, bytes, offset)
}

#[cfg(windows)]
fn read_file_at(file: &File, bytes: &mut [u8], offset: u64) -> io::Result<usize> {
    std::os::windows::fs::FileExt::seek_read(file, bytes, offset)
}

fn validate_read_block_len(len: usize) -> io::Result<()> {
    if !(1..=FILE_READ_CHUNK_SIZE).contains(&len) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("file read block length must be between 1 and {FILE_READ_CHUNK_SIZE}"),
        ));
    }
    Ok(())
}

fn validate_write_block_len(len: usize) -> io::Result<()> {
    if !(1..=FILE_WRITE_CHUNK_SIZE).contains(&len) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("file write block length must be between 1 and {FILE_WRITE_CHUNK_SIZE}"),
        ));
    }
    Ok(())
}

/// 定位写的区间上限校验（nova 从 write_block 抽出与 writeStream 共享）：
/// 原生文件偏移有符号——Windows 把负 offset 当哨兵值，写区间不得超 i64 上限。
fn checked_write_end(offset: u64, bytes_len: usize) -> io::Result<u64> {
    offset
        .checked_add(bytes_len as u64)
        .and_then(|end| i64::try_from(end).ok())
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "file write range exceeds the signed 64-bit file offset limit",
            )
        })
        .map(|end| end as u64)
}

/// fs/writeBlock 与 fs/writeStream 共享的定位写核心（nova 自有件——writeStream
/// 为 nova 自有通道，上游无对应）：spawn_blocking 内 write_at|seek_write 循环
/// 写满为止；返回即全部落定（同步 syscall，无 tokio 写管线缓冲）。
async fn write_block_at(file: Arc<File>, offset: u64, bytes: Vec<u8>) -> io::Result<()> {
    let end = checked_write_end(offset, bytes.len())?;
    tokio::task::spawn_blocking(move || {
        let mut position = offset;
        while position < end {
            let written = (position - offset) as usize;
            #[cfg(unix)]
            let result =
                std::os::unix::fs::FileExt::write_at(file.as_ref(), &bytes[written..], position);
            #[cfg(windows)]
            let result = std::os::windows::fs::FileExt::seek_write(
                file.as_ref(),
                &bytes[written..],
                position,
            );
            match result {
                Ok(0) => {
                    return Err(io::Error::new(
                        io::ErrorKind::WriteZero,
                        "failed to write file block",
                    ));
                }
                Ok(count) => position += count as u64,
                Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
                Err(error) => return Err(error),
            }
        }
        Ok(())
    })
    .await
    .map_err(|error| io::Error::other(format!("file write task stopped unexpectedly: {error}")))?
}

fn unknown_handle_error(handle_id: &str) -> io::Error {
    io::Error::new(
        io::ErrorKind::NotFound,
        format!("unknown file handle `{handle_id}`"),
    )
}

#[cfg(test)]
#[path = "file_handle_tests.rs"]
mod tests;
