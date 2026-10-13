use nova_exec_server_utils_path_uri::PathUri;
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
/// 消费者中途放弃（未读到 Done 即 drop）时，best-effort 向服务端发
/// fs/close 收摊（对位 codex `FileReadRegistration::drop`——拉模式放弃即关
/// 句柄，服务端立刻停读；推送模式不补这一手，服务端会把整个文件读完并
/// 推入无人接收的路由，槽位/fd 一直被占到流结束）。正常读到 Done 的完成
/// 路径经 `active = false` 跳过（服务端已自行收摊）。
struct PushReadRegistration {
    client: ExecServerClient,
    handle_id: String,
    active: bool,
    runtime: Option<tokio::runtime::Handle>,
}

impl Drop for PushReadRegistration {
    fn drop(&mut self) {
        self.client.remove_fs_read_stream(&self.handle_id);
        if !self.active {
            return;
        }
        let client = self.client.clone();
        let handle_id = self.handle_id.clone();
        let runtime = self
            .runtime
            .clone()
            .or_else(|| tokio::runtime::Handle::try_current().ok());
        if let Some(runtime) = runtime {
            runtime.spawn(async move {
                let _ = client
                    .fs_close(crate::protocol::FsCloseParams { handle_id })
                    .await;
            });
        }
    }
}

/// 经 fs/readStream 端点打开推送式流式读：客户端先注册推送路由再发请求，
/// 服务端随后逐块推送（`fs/readStream/chunk`），以 `fs/readStream/done` 收尾。
/// 平台沙箱由服务端经沙箱开门（fd 传递）承载，客户端无感。
///
/// 对位 codex 841b5490b2 `file_stream::open`：sandbox 借引用传入、发送时克隆。
pub(super) async fn open_push(
    client: ExecServerClient,
    path: PathUri,
    sandbox: Option<&FileSystemSandboxContext>,
) -> FileSystemResult<FileSystemReadStream> {
    let registration = PushReadRegistration {
        client,
        handle_id: Uuid::new_v4().simple().to_string(),
        active: true,
        runtime: tokio::runtime::Handle::try_current().ok(),
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
            sandbox: sandbox.cloned(),
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
        |(mut receiver, mut registration): (
            mpsc::Receiver<FsReadStreamEvent>,
            PushReadRegistration,
        )| async move {
            match receiver.recv().await {
                Some(FsReadStreamEvent::Chunk(chunk)) => {
                    Ok(Some((chunk, (receiver, registration))))
                }
                Some(FsReadStreamEvent::Done) => {
                    // 正常完成：服务端已自行收摊，drop 不再发 fs/close
                    registration.active = false;
                    // 显式 drop 让 active 赋值被消费（rustc 不把隐式 Drop 当读，
                    // 裸赋值会触发 unused_assignments 假阳性）
                    drop(registration);
                    Ok(None)
                }
                Some(FsReadStreamEvent::Failed(message)) => {
                    // 服务端错误终态同样已收摊
                    registration.active = false;
                    drop(registration);
                    Err(io::Error::new(
                        io::ErrorKind::Other,
                        format!("fs/readStream failed: {message}"),
                    ))
                }
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
