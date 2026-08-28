use nova_executor_utils_path_uri::PathUri;
use tokio::io;
use tokio::sync::mpsc;
use uuid::Uuid;

use super::map_remote_error;
use crate::ExecServerClient;
use crate::FileSystemReadStream;
use crate::FileSystemResult;
use crate::FileSystemSandboxContext;
use crate::client::FsReadStreamEvent;
use crate::protocol::FsReadStreamParams;

/// 推送式流式读的注册句柄：流被丢弃时注销路由，
/// 避免服务端后续推送在注册表里找不到归属。
struct PushReadRegistration {
    client: ExecServerClient,
    handle_id: String,
}

impl Drop for PushReadRegistration {
    fn drop(&mut self) {
        self.client.remove_fs_read_stream(&self.handle_id);
    }
}

/// 经 fs/readStream 端点打开推送式流式读：客户端先注册推送路由再发请求，
/// 服务端随后逐块推送（`fs/readStream/chunk`），以 `fs/readStream/done` 收尾。
/// 平台沙箱由服务端经沙箱开门（fd 传递）承载，客户端无感。
pub(super) async fn open_push(
    client: ExecServerClient,
    path: PathUri,
    sandbox: Option<FileSystemSandboxContext>,
) -> FileSystemResult<FileSystemReadStream> {
    let registration = PushReadRegistration {
        client,
        handle_id: Uuid::new_v4().simple().to_string(),
    };
    // 先注册路由再发请求：服务端响应后即开始推送，注册不能比推送晚到。
    let receiver = registration
        .client
        .register_fs_read_stream(registration.handle_id.clone())
        .map_err(map_remote_error)?;
    if let Err(error) = registration
        .client
        .fs_read_stream(FsReadStreamParams {
            handle_id: registration.handle_id.clone(),
            path,
            offset: 0,
            len: None,
            block_size: None,
            sandbox,
        })
        .await
    {
        registration
            .client
            .remove_fs_read_stream(&registration.handle_id);
        return Err(map_remote_error(error));
    }
    Ok(FileSystemReadStream::new(futures::stream::try_unfold(
        (receiver, registration),
        |(mut receiver, registration): (
            mpsc::Receiver<FsReadStreamEvent>,
            PushReadRegistration,
        )| async move {
            match receiver.recv().await {
                Some(FsReadStreamEvent::Chunk(chunk)) => {
                    Ok(Some((chunk, (receiver, registration))))
                }
                Some(FsReadStreamEvent::Done) => Ok(None),
                Some(FsReadStreamEvent::Failed(message)) => Err(io::Error::new(
                    io::ErrorKind::Other,
                    format!("fs/readStream failed: {message}"),
                )),
                // 发送端在 Done 之前全部 drop：连接异常断开的兜底（正常路径
                // fail_all_fs_read_streams 会先推 Failed；这里防路由被绕过）。
                None => Err(io::Error::new(
                    io::ErrorKind::UnexpectedEof,
                    "fs/readStream ended without a done notification",
                )),
            }
        },
    )))
}
