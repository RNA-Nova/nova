use std::collections::HashMap;
use std::fs::File;
use std::io;
use std::sync::Arc;

use nova_executor_file_system::FILE_READ_CHUNK_SIZE;
use tokio::sync::Mutex;

const MAX_OPEN_FILE_READS: usize = 128;

pub(crate) const DEFAULT_READ_STREAM_BLOCK_SIZE: usize = 256 * 1024; // 256KB
pub(crate) const MAX_READ_STREAM_BLOCK_SIZE: usize = 4 * 1024 * 1024; // 4MB

#[derive(Debug, Eq, PartialEq)]
pub(crate) struct FileReadBlock {
    pub(crate) bytes: Vec<u8>,
    pub(crate) eof: bool,
}

/// 已打开读句柄表：沙箱化 fs/readStream 也由 executor 进程自持句柄
/// （一次性 helper 开门后把 fd/handle 传回，见 sandboxed_file_open），
/// 因此句柄统一为普通 file 对象。
#[derive(Clone, Default)]
pub(crate) struct FileReadHandleManager {
    handles: Arc<Mutex<HashMap<String, Arc<File>>>>,
}

impl FileReadHandleManager {
    pub(crate) async fn open(
        &self,
        handle_id: String,
        file: tokio::fs::File,
    ) -> io::Result<String> {
        let file = Arc::new(file.into_std().await);
        let mut handles = self.handles.lock().await;
        check_handle_slot(&handles, &handle_id)?;
        handles.insert(handle_id.clone(), file);
        Ok(handle_id)
    }

    pub(crate) async fn read_block(
        &self,
        handle_id: &str,
        offset: u64,
        len: usize,
    ) -> io::Result<FileReadBlock> {
        validate_read_block_len(len)?;
        let file = {
            let handles = self.handles.lock().await;
            match handles.get(handle_id) {
                Some(file) => Arc::clone(file),
                None => return Err(unknown_handle_error(handle_id)),
            }
        };
        let result =
            match tokio::task::spawn_blocking(move || read_block_at(&file, offset, len)).await {
                Ok(result) => result,
                Err(error) => Err(io::Error::other(format!(
                    "file read task stopped unexpectedly: {error}"
                ))),
            };
        if result.is_err() {
            self.close(handle_id).await;
        }
        result
    }

    pub(crate) async fn close(&self, handle_id: &str) {
        self.handles.lock().await.remove(handle_id);
    }

    pub(crate) async fn close_all(&self) {
        self.handles.lock().await.clear();
    }

    #[cfg(test)]
    pub(crate) async fn open_handle_count(&self) -> usize {
        self.handles.lock().await.len()
    }
}

fn check_handle_slot(handles: &HashMap<String, Arc<File>>, handle_id: &str) -> io::Result<()> {
    if handles.contains_key(handle_id) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("file read handle `{handle_id}` already exists"),
        ));
    }
    if handles.len() >= MAX_OPEN_FILE_READS {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("at most {MAX_OPEN_FILE_READS} file reads may be open per connection"),
        ));
    }
    Ok(())
}

/// fs/readStream 的共享流式读循环：executor 自读路径与沙箱开门（fd 传递）
/// 路径共用同一份 offset/len/eof 语义，保证两种执行体线上行为一致。
///
/// 逐块读取已注册句柄并经 `emit_chunk(seq, bytes, eof)` 推出；`emit_chunk`
/// 返回 false 表示对端已消失，循环静默停止（不再读取，error 保持 None）。
/// 返回 `(total_bytes, error)`，与 fs/readStream/done 通知的载荷一致。
pub(crate) async fn stream_file_blocks<F>(
    file_reads: &FileReadHandleManager,
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

        match file_reads
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

fn unknown_handle_error(handle_id: &str) -> io::Error {
    io::Error::new(
        io::ErrorKind::NotFound,
        format!("unknown file read handle `{handle_id}`"),
    )
}
