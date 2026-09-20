use std::io;
use std::path::Path;
use tokio::io::AsyncReadExt as _;

pub(crate) async fn open(path: &Path) -> io::Result<tokio::fs::File> {
    reject_named_pipe(path)?;
    let mut options = tokio::fs::OpenOptions::new();
    options.read(true);
    configure_open(&mut options);

    let file = options.open(path).await?;
    if !is_disk_file(&file) || !file.metadata().await?.is_file() {
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
    let mut options = tokio::fs::OpenOptions::new();
    options.read(true);
    configure_open(&mut options);

    #[cfg(unix)]
    options.custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW);

    #[cfg(windows)]
    {
        use windows_sys::Win32::Storage::FileSystem::FILE_FLAG_OPEN_REPARSE_POINT;

        options.custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
    }

    let mut file = options.open(path).await?;
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

/// 以写模式打开（创建/截断）普通文件，与 `fs/writeFile` 的整文件覆写语义一致。
///
/// 已存在的路径必须是普通文件——拒绝向目录、设备或 FIFO 等特殊文件截断写入。
pub(crate) async fn create(path: &Path) -> io::Result<tokio::fs::File> {
    reject_named_pipe(path)?;
    let mut options = tokio::fs::OpenOptions::new();
    options.write(true).create(true).truncate(true);
    configure_open(&mut options);

    let file = options.open(path).await?;
    if !is_disk_file(&file) || !file.metadata().await?.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("path `{}` is not a file", path.display()),
        ));
    }
    Ok(file)
}

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
fn configure_open(options: &mut tokio::fs::OpenOptions) {
    options.custom_flags(libc::O_NONBLOCK);
}

#[cfg(windows)]
fn configure_open(options: &mut tokio::fs::OpenOptions) {
    use windows_sys::Win32::Storage::FileSystem::SECURITY_IDENTIFICATION;

    options.security_qos_flags(SECURITY_IDENTIFICATION);
}

#[cfg(not(any(unix, windows)))]
fn configure_open(_options: &mut tokio::fs::OpenOptions) {}

#[cfg(windows)]
pub(crate) fn is_disk_file(file: &impl std::os::windows::io::AsRawHandle) -> bool {
    use windows_sys::Win32::Foundation::HANDLE;
    use windows_sys::Win32::Storage::FileSystem::FILE_TYPE_DISK;
    use windows_sys::Win32::Storage::FileSystem::GetFileType;

    // SAFETY: `file` owns this handle for the duration of the call.
    unsafe { GetFileType(file.as_raw_handle() as HANDLE) == FILE_TYPE_DISK }
}

#[cfg(not(windows))]
fn is_disk_file(_file: &tokio::fs::File) -> bool {
    true
}

#[cfg(test)]
#[path = "regular_file_tests.rs"]
mod tests;
