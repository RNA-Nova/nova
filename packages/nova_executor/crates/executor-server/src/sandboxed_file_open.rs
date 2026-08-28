use nova_executor_protocol::JSONRPCErrorError;
use nova_executor_sandboxing::SandboxExecRequest;
use nova_executor_utils_path_uri::PathUri;
use tokio::io;

use crate::fs_helper::FsHelperOpenResponse;
use crate::fs_helper::FsHelperRequest;
use crate::fs_helper::FsHelperResponse;
#[cfg(windows)]
use crate::fs_sandbox::drain_helper_stderr;
use crate::fs_sandbox::io_error;
#[cfg(windows)]
use crate::fs_sandbox::read_helper_response;
#[cfg(windows)]
use crate::fs_sandbox::reap_helper_after_response;
use crate::fs_sandbox::spawn_command;
#[cfg(unix)]
use crate::fs_sandbox::wait_for_helper_output;
use crate::protocol::FsReadFileParams;
use crate::rpc::internal_error;

/// 沙箱化开门（fs/readStream 的读端执行体）：拉起一次性沙箱 helper 在平台沙箱
/// 内 open 目标文件，把文件描述符传回主进程（Unix 经 SCM_RIGHTS / Windows 经
/// 句柄复制），主进程自持句柄自行读取——helper 随即退出，不再长命推流。
pub(crate) async fn open(
    command: SandboxExecRequest,
    path: PathUri,
) -> Result<tokio::fs::File, JSONRPCErrorError> {
    let request = serde_json::to_vec(&FsHelperRequest::Open(FsReadFileParams {
        path,
        follow_symlinks: None,
        sandbox: None,
    }))
    .map_err(|error| internal_error(format!("invalid fs sandbox helper request: {error}")))?;
    open_platform(command, request).await
}

fn open_response(response: &[u8]) -> Result<FsHelperOpenResponse, JSONRPCErrorError> {
    let response: FsHelperResponse = serde_json::from_slice(response).map_err(|error| {
        internal_error(format!("invalid fs sandbox helper open response: {error}"))
    })?;
    match response {
        FsHelperResponse::Ok(payload) => payload.expect_open(),
        FsHelperResponse::Error(error) => Err(error),
    }
}

// Unix 经 helper 的 stdin socketpair 传递已打开的 fd。
#[cfg(unix)]
async fn open_platform(
    command: SandboxExecRequest,
    request: Vec<u8>,
) -> Result<tokio::fs::File, JSONRPCErrorError> {
    use std::io::Write;
    use std::os::fd::OwnedFd;
    use std::os::unix::net::UnixStream;

    let (mut receiver, sender) = UnixStream::pair().map_err(io_error)?;
    let sender: OwnedFd = sender.into();
    let child = spawn_command(command, std::process::Stdio::from(sender))?;
    receiver.write_all(&request).map_err(io_error)?;
    receiver
        .shutdown(std::net::Shutdown::Write)
        .map_err(io_error)?;

    // helper 退出后其传出的 fd 仍由内核保管在 socket 缓冲里，recvmsg 随时可收
    let output = wait_for_helper_output(child).await?;
    open_response(&output.stdout)?;
    let descriptor = receive_file_descriptor(&receiver).map_err(io_error)?;
    Ok(tokio::fs::File::from_std(std::fs::File::from(descriptor)))
}

// Windows 在 helper 退出前从其进程复制句柄。
#[cfg(windows)]
async fn open_platform(
    command: SandboxExecRequest,
    mut request: Vec<u8>,
) -> Result<tokio::fs::File, JSONRPCErrorError> {
    use tokio::io::AsyncWriteExt;

    let mut child = spawn_command(command, std::process::Stdio::piped())?;
    let mut stdin = child
        .stdin
        .take()
        .ok_or_else(|| internal_error("missing fs sandbox helper stdin".to_string()))?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| internal_error("missing fs sandbox helper stdout".to_string()))?;
    request.push(b'\n');
    stdin.write_all(&request).await.map_err(io_error)?;
    stdin.flush().await.map_err(io_error)?;
    let stderr = drain_helper_stderr(&mut child);

    let result = async {
        let response = read_helper_response(stdout).await?;
        let response = open_response(&response)?;
        duplicate_file_handle(response.process_id, response.file_handle).map_err(io_error)
    }
    .await;
    // 关闭 stdin 即 ack：helper 收到 EOF 后才放行已打开的句柄并退出
    drop(stdin);
    reap_helper_after_response(child, stderr).await?;
    result.map(tokio::fs::File::from_std)
}

// SCM_RIGHTS 仅 Unix 可用（helper 侧调用：把 fd 经 stdin socket 递给父进程）。
#[cfg(unix)]
pub(crate) fn transfer_file(file: &tokio::fs::File) -> io::Result<()> {
    use rustix::net::SendAncillaryBuffer;
    use rustix::net::SendAncillaryMessage;
    use rustix::net::SendFlags;
    use std::io::IoSlice;
    use std::os::fd::AsFd;

    let descriptors = [file.as_fd()];
    let mut space = [std::mem::MaybeUninit::uninit(); rustix::cmsg_space!(ScmRights(1))];
    let mut control = SendAncillaryBuffer::new(&mut space);
    if !control.push(SendAncillaryMessage::ScmRights(&descriptors)) {
        return Err(io::Error::other("missing file-descriptor control header"));
    }
    if rustix::net::sendmsg(
        std::io::stdin(),
        &[IoSlice::new(&[0])],
        &mut control,
        SendFlags::empty(),
    )? != 1
    {
        return Err(io::Error::other(
            "fs sandbox helper did not transfer its opened file descriptor",
        ));
    }
    Ok(())
}

// fd 传递仅 Unix 可用（主进程侧接收）。
#[cfg(unix)]
fn receive_file_descriptor(
    socket: &std::os::unix::net::UnixStream,
) -> io::Result<std::os::fd::OwnedFd> {
    use rustix::net::RecvAncillaryBuffer;
    use rustix::net::RecvAncillaryMessage;
    use rustix::net::RecvFlags;
    use rustix::net::ReturnFlags;
    use std::io::IoSliceMut;

    let mut byte = [0_u8];
    let mut buffers = [IoSliceMut::new(&mut byte)];
    let mut space = [std::mem::MaybeUninit::uninit(); rustix::cmsg_space!(ScmRights(1))];
    let mut control = RecvAncillaryBuffer::new(&mut space);
    // Linux 可以在接收 fd 时原子设置 close-on-exec。
    #[cfg(target_os = "linux")]
    let flags = RecvFlags::CMSG_CLOEXEC;
    // 其他 Unix 平台需要下面的非原子 fcntl 调用。
    #[cfg(not(target_os = "linux"))]
    let flags = RecvFlags::empty();
    let message = rustix::net::recvmsg(socket, &mut buffers, &mut control, flags)?;
    // 校验 ancillary 数据形状：恰好 1 字节载荷且控制消息未被截断
    if message.bytes != 1 || message.flags.contains(ReturnFlags::CTRUNC) {
        return Err(io::Error::other("invalid file-descriptor control message"));
    }
    let descriptor = control
        .drain()
        .find_map(|message| match message {
            RecvAncillaryMessage::ScmRights(mut descriptors) => descriptors.next(),
            _ => None,
        })
        .ok_or_else(|| io::Error::other("missing transferred file descriptor"))?;
    // macOS 无法原子设置 close-on-exec，fd 有短暂的可继承窗口；helper 启动时
    // 关闭继承 fd 以压缩该窗口（见 fs_sandbox::spawn_command 的 pre_exec）。
    #[cfg(not(target_os = "linux"))]
    rustix::io::fcntl_setfd(&descriptor, rustix::io::FdFlags::CLOEXEC)?;
    Ok(descriptor)
}

// Windows 的文件句柄必须跨进程复制。
#[cfg(windows)]
fn duplicate_file_handle(process_id: u32, file_handle: u64) -> io::Result<std::fs::File> {
    use std::os::windows::io::AsRawHandle;
    use std::os::windows::io::FromRawHandle;
    use std::os::windows::io::OwnedHandle;
    use windows_sys::Win32::Foundation::DUPLICATE_SAME_ACCESS;
    use windows_sys::Win32::Foundation::DuplicateHandle;
    use windows_sys::Win32::Foundation::HANDLE;
    use windows_sys::Win32::System::Threading::GetCurrentProcess;
    use windows_sys::Win32::System::Threading::OpenProcess;
    use windows_sys::Win32::System::Threading::PROCESS_DUP_HANDLE;

    // SAFETY: OpenProcess 成功返回受拥有的句柄，失败返回 null。
    let process = unsafe { OpenProcess(PROCESS_DUP_HANDLE, 0, process_id) };
    if process == 0 {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: 上面的 OpenProcess 成功结果归本作用域所有。
    let process = unsafe { OwnedHandle::from_raw_handle(process as _) };
    let mut duplicated: HANDLE = 0;
    // SAFETY: 两个进程句柄均有效，duplicated 接收受拥有的文件句柄。
    if unsafe {
        DuplicateHandle(
            process.as_raw_handle() as HANDLE,
            file_handle as HANDLE,
            GetCurrentProcess(),
            &raw mut duplicated,
            0,
            0,
            DUPLICATE_SAME_ACCESS,
        )
    } == 0
    {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: DuplicateHandle 已把新文件句柄的所有权转移过来。
    Ok(unsafe { std::fs::File::from_raw_handle(duplicated as _) })
}
