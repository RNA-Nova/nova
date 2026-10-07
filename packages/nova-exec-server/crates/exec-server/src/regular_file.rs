use std::io;
#[cfg(unix)]
use std::os::unix::fs::OpenOptionsExt;
#[cfg(windows)]
use std::os::windows::fs::OpenOptionsExt;
use std::path::Path;
use tokio::io::AsyncReadExt as _;

use crate::protocol::FsOpenMode;

/// 开门模式（nova 内部件）：线上 fs/open 的 `FsOpenMode`（read|replace）之外
/// 补 `Resume`（不截断写打开）——fs/writeStream 的 offset 续传与 fs helper
/// IPC（nova 自有协议，见 fs_helper.rs `FS_HELPER_IPC_PROTOCOL_VERSION`）共用
/// 此形态；serde 形状供 helper IPC 使用。
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) enum OpenMode {
    /// 打开既有文件只读（缺省，与线上 fs/open 同）
    #[default]
    Read,
    Replace,
    /// 不截断写打开（write+create，无 truncate）——断点续传从既有内容尾部续写
    Resume,
}

impl From<FsOpenMode> for OpenMode {
    fn from(mode: FsOpenMode) -> Self {
        match mode {
            FsOpenMode::Read => Self::Read,
            FsOpenMode::Replace => Self::Replace,
        }
    }
}

/// 对位 codex c39bfa4c8f `regular_file::open(path, mode)`：spawn_blocking 包同步开。
pub(crate) async fn open(path: &Path, mode: OpenMode) -> io::Result<tokio::fs::File> {
    let path = path.to_path_buf();
    tokio::task::spawn_blocking(move || open_sync(&path, mode).map(tokio::fs::File::from_std))
        .await
        .map_err(|error| io::Error::other(format!("filesystem task failed: {error}")))?
}

/// Opens a regular file without blocking on Unix FIFOs or impersonating Windows pipe servers.
/// （对位 codex `open_sync(path, mode)`：Replace 时 write+create+truncate；
/// nova 补 Resume：write+create 不截断——断点续传形态）
pub(crate) fn open_sync(path: &Path, mode: OpenMode) -> io::Result<std::fs::File> {
    reject_named_pipe(path)?;
    let mut options = std::fs::OpenOptions::new();
    match mode {
        OpenMode::Read => {
            options.read(true);
        }
        OpenMode::Replace => {
            options.write(true).create(true).truncate(true);
        }
        OpenMode::Resume => {
            options.write(true).create(true);
        }
    }
    configure_open(&mut options);

    let file = options.open(path)?;
    if !is_disk_file(&file) || !file.metadata()?.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("path `{}` is not a file", path.display()),
        ));
    }
    Ok(file)
}

/// 读取敏感小文件（配置/角色文件）：拒绝符号链接/命名管道/非磁盘文件。
/// 供 agent 层读角色文件等场景使用（对位 codex 同名公开工具）。
pub async fn read_sensitive_file_to_string(path: &Path) -> io::Result<String> {
    let mut options = std::fs::OpenOptions::new();
    options.read(true);
    configure_open(&mut options);

    #[cfg(unix)]
    options.custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW);

    #[cfg(windows)]
    {
        use windows_sys::Win32::Storage::FileSystem::FILE_FLAG_OPEN_REPARSE_POINT;

        options.custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
    }

    let mut file = tokio::fs::OpenOptions::from(options).open(path).await?;
    let metadata = file.metadata().await?;
    if !is_disk_file(&file) || !metadata.is_file() || metadata.file_type().is_symlink() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("path `{}` is not a regular file", path.display()),
        ));
    }

    let mut contents = String::new();
    file.read_to_string(&mut contents).await?;
    Ok(contents)
}

// nova 自有加固（上游无此步）：windows 命名管道不能当普通文件读写。
#[cfg(windows)]
fn reject_named_pipe(path: &Path) -> io::Result<()> {
    // windows 命名管道不能当普通文件读写：客户端打开动作本身可能因 DACL/QoS
    // 标志失败并映射为不可预测的错误类别，先行显式拒绝，保证与 unix 的 FIFO
    // 拒绝一样稳定返回 InvalidInput。
    let text = path.as_os_str().to_string_lossy();
    if text.starts_with(r"\\.\pipe\") || text.starts_with(r"\\?\pipe\") {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("path `{}` is a named pipe", path.display()),
        ));
    }
    Ok(())
}

#[cfg(not(windows))]
fn reject_named_pipe(_path: &Path) -> io::Result<()> {
    Ok(())
}

#[cfg(unix)]
fn configure_open(options: &mut std::fs::OpenOptions) {
    options.custom_flags(libc::O_NONBLOCK);
}

#[cfg(windows)]
fn configure_open(options: &mut std::fs::OpenOptions) {
    use windows_sys::Win32::Storage::FileSystem::SECURITY_IDENTIFICATION;

    options.security_qos_flags(SECURITY_IDENTIFICATION);
}

#[cfg(not(any(unix, windows)))]
fn configure_open(_options: &mut std::fs::OpenOptions) {}

#[cfg(windows)]
pub(crate) fn is_disk_file(file: &impl std::os::windows::io::AsRawHandle) -> bool {
    use windows_sys::Win32::Foundation::HANDLE;
    use windows_sys::Win32::Storage::FileSystem::FILE_TYPE_DISK;
    use windows_sys::Win32::Storage::FileSystem::GetFileType;

    // SAFETY: `file` owns this handle for the duration of the call.
    unsafe { GetFileType(file.as_raw_handle() as HANDLE) == FILE_TYPE_DISK }
}

#[cfg(not(windows))]
fn is_disk_file<T>(_file: &T) -> bool {
    true
}

#[cfg(test)]
#[path = "regular_file_tests.rs"]
mod tests;
