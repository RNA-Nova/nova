use std::io;
use std::path::Path;

pub(crate) async fn open(path: &Path) -> io::Result<tokio::fs::File> {
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

/// 以写模式打开（创建/截断）普通文件，与 `fs/writeFile` 的整文件覆写语义一致。
///
/// 已存在的路径必须是普通文件——拒绝向目录、设备或 FIFO 等特殊文件截断写入。
pub(crate) async fn create(path: &Path) -> io::Result<tokio::fs::File> {
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
