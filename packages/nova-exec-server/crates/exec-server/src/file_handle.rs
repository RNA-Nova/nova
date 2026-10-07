//! 连接级文件句柄表：定位读/写均有界（对位 codex d25c114d49+c39bfa4c8f
//! `file_handle.rs`）。信号量槽位同时约束已注册句柄与在飞行打开；
//! 句柄表用同步锁，锁不跨 await。
//!
//! 沙箱化 fs/open|readStream 也由 executor 进程自持句柄（一次性 helper 开门后
//! 把 fd/handle 传回，见 sandboxed_file_open），因此句柄统一为普通 file 对象。
//!
//! v1.12 起 fs/writeStream 收编进本表（nova 自有通道）：与 readBlock/writeBlock
//! 共享句柄容量/ID 校验，chunk 经与 write_block 共享的定位写核心落到每流独立
//! 的追加游标（`WriteStreamEntryState` 随句柄条目持有）。

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

/// fs/writeStream 句柄的每流追加状态（nova 自有通道，随句柄条目持有）：
/// 定位写游标（开门起点 + 已写累计）+ seq 严格序 + eof 序 + 首个失败
/// （通知无回执——协议违约/写盘失败记为终态，由随后的 done 回报）。
struct WriteStreamEntryState {
    expected_seq: u64,
    cursor: u64,
    eof_seen: bool,
    failed: Option<(io::ErrorKind, String)>,
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

    /// fs/writeStream 开门注册（nova 自有通道）：与 `open` 同一套容量/ID 校验，
    /// 条目携带每流追加游标（`offset` = 续传起点；None = 创建/截断语义）。
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
        let mut handles = self.lock_handles();
        let Entry::Vacant(entry) = handles.entry(handle_id.clone()) else {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                format!("file handle `{handle_id}` already exists"),
            ));
        };
        entry.insert(FileHandleEntry {
            file,
            write_stream: write_stream_offset.map(|offset| WriteStreamEntryState {
                expected_seq: 0,
                cursor: offset,
                eof_seen: false,
                failed: None,
            }),
            _permit: permit,
        });
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

    /// fs/writeStream/chunk 执行体（nova 自有通道，v1.12 起收编句柄族）：
    /// seq 严格序 + eof 序 + 块长（≤MAX_WRITE_STREAM_CHUNK_BYTES）校验，定位写经
    /// 共享核心落到每流独立的追加游标。chunk 是通知（无回执）：协议违约/写盘
    /// 失败只把流转入失败终态，由随后的 done 回报首个错误。**中断语义翻转
    /// （v1.12）：失败/中止不再删半成品——文件留在盘上，为断点续传让路**。
    pub(crate) async fn write_stream_chunk(
        &self,
        handle_id: &str,
        seq: u64,
        bytes: Vec<u8>,
        eof: bool,
    ) {
        let assignment = {
            let mut handles = self.lock_handles();
            let Some(entry) = handles.get_mut(handle_id) else {
                // 未知句柄：流可能已完成或从未建立；通知无回执，忽略即可
                tracing::warn!("ignoring write stream chunk for unknown handle `{handle_id}`");
                return;
            };
            let Some(state) = entry.write_stream.as_mut() else {
                tracing::warn!(
                    "ignoring write stream chunk for non-stream handle `{handle_id}`"
                );
                return;
            };
            if state.failed.is_some() {
                // 失败终态：静默忽略后续块，等 done 回报首个错误
                return;
            }
            let violation = if state.eof_seen {
                Some(format!(
                    "file write stream `{handle_id}` received chunk after eof"
                ))
            } else if seq != state.expected_seq {
                Some(format!(
                    "file write stream `{handle_id}` expected seq {}, got {seq}",
                    state.expected_seq
                ))
            } else if bytes.len() > MAX_WRITE_STREAM_CHUNK_BYTES {
                Some(format!(
                    "file write stream chunk must not exceed {MAX_WRITE_STREAM_CHUNK_BYTES} bytes"
                ))
            } else {
                None
            };
            if let Some(error) = violation {
                state.failed = Some((io::ErrorKind::InvalidInput, error));
                return;
            }
            let offset = state.cursor;
            state.cursor = state.cursor.saturating_add(bytes.len() as u64);
            state.expected_seq += 1;
            state.eof_seen = eof;
            (Arc::clone(&entry.file), offset)
        };
        // 空块（eof 哨兵）只携带标志位，不落盘
        if bytes.is_empty() {
            return;
        }
        if let Err(err) = write_block_at(assignment.0, assignment.1, bytes).await {
            if let Some(state) = self
                .lock_handles()
                .get_mut(handle_id)
                .and_then(|entry| entry.write_stream.as_mut())
            {
                state.failed = Some((
                    err.kind(),
                    format!("file write stream `{handle_id}` write failed: {err}"),
                ));
            }
        }
    }

    /// fs/writeStream/done 执行体：流须已见 eof 块；失败终态回报首个错误。
    /// 成功返回全量字节数（offset 起点 + 本次流式字节数——追加游标的终点，
    /// 客户端对账用），句柄摘除；无论成败文件均留在盘上（v1.12 语义翻转）。
    pub(crate) async fn finish_write_stream(&self, handle_id: &str) -> io::Result<u64> {
        let entry = self.lock_handles().remove(handle_id);
        let Some(entry) = entry else {
            return Err(unknown_handle_error(handle_id));
        };
        let Some(state) = entry.write_stream else {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                format!("file handle `{handle_id}` is not a write stream"),
            ));
        };
        if let Some((kind, error)) = state.failed {
            return Err(io::Error::new(kind, error));
        }
        if !state.eof_seen {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                format!("file write stream `{handle_id}` finished without an eof chunk"),
            ));
        }
        // 定位写经 spawn_blocking 内同步 write 落定，返回即全部落盘——
        // 无 tokio 写管线缓冲，无需 flush 等待
        Ok(state.cursor)
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
    /// fs/writeStream 的每流追加状态（v1.12 起写流收编句柄族；None = 非流句柄）
    write_stream: Option<WriteStreamEntryState>,
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
