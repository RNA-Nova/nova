use std::error::Error;

use tokio::io;
use tokio::io::AsyncBufReadExt;
use tokio::io::AsyncWriteExt;
use tokio::io::BufReader;

use crate::fs_helper::FsHelperOpenResponse;
use crate::fs_helper::FsHelperPayload;
use crate::fs_helper::FsHelperRequest;
use crate::fs_helper::FsHelperResponse;
use crate::fs_helper::map_fs_error;
use crate::fs_helper::run_direct_request;
use crate::fs_helper::run_write_stream_request;
use crate::regular_file;

pub fn main() -> ! {
    let exit_code = match tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
    {
        Ok(runtime) => match runtime.block_on(run_main()) {
            Ok(()) => 0,
            Err(err) => {
                eprintln!("fs sandbox helper failed: {err}");
                1
            }
        },
        Err(err) => {
            eprintln!("failed to start fs sandbox helper runtime: {err}");
            1
        }
    };
    std::process::exit(exit_code);
}

async fn run_main() -> Result<(), Box<dyn Error + Send + Sync>> {
    // stdin 行帧协议（NDJSON）：首行为请求帧；长命流式写随后逐行收事件帧，
    // 一次性与 Open 请求只含首行（executor 写完即关闭 stdin，EOF 收尾首行）。
    let mut stdin_lines = BufReader::new(io::stdin()).lines();
    let request_line = stdin_lines
        .next_line()
        .await?
        .ok_or_else(|| io::Error::new(io::ErrorKind::UnexpectedEof, "missing fs helper request"))?;
    let request: FsHelperRequest = serde_json::from_str(&request_line)?;
    let mut stdout = io::stdout();
    // Open 请求打开的文件须保活到应答写完：Unix 在写应答前已把 fd 经
    // SCM_RIGHTS 递给父进程；Windows 则须等父进程复制完句柄（见文末 ack 等待）。
    let mut opened_file = None;
    match request {
        // 一次性开门：在沙箱内 open 后把 fd/handle 传回父进程，随即退出
        FsHelperRequest::Open(params) => {
            let result: io::Result<_> = async {
                let path = params.path.to_abs_path()?;
                let file = regular_file::open(path.as_path()).await?;
                // Unix 直接把已打开的 fd 经 stdin socket 递给父进程。
                #[cfg(unix)]
                crate::sandboxed_file_open::transfer_file(&file)?;
                let response = FsHelperOpenResponse {
                    // Windows 由父进程从 helper 进程复制句柄。
                    #[cfg(windows)]
                    process_id: std::process::id(),
                    // 父进程需要原始句柄值来完成复制。
                    #[cfg(windows)]
                    file_handle: {
                        use std::os::windows::io::AsRawHandle;

                        file.as_raw_handle() as usize as u64
                    },
                };
                opened_file = Some(file);
                Ok(FsHelperPayload::Open(response))
            }
            .await;
            let response = match result {
                Ok(payload) => FsHelperResponse::Ok(payload),
                Err(error) => FsHelperResponse::Error(map_fs_error(error)),
            };
            stdout
                .write_all(serde_json::to_string(&response)?.as_bytes())
                .await?;
            stdout.write_all(b"\n").await?;
        }
        // 长命流式写：首行启动握手，stdin 逐行收 chunk/finish 事件帧，
        // 末行回传最终确认，进程活到流结束
        FsHelperRequest::WriteStream(params) => {
            run_write_stream_request(params, &mut stdin_lines, &mut stdout).await?;
        }
        request => {
            let response = match run_direct_request(request).await {
                Ok(payload) => FsHelperResponse::Ok(payload),
                Err(error) => FsHelperResponse::Error(error),
            };
            stdout
                .write_all(serde_json::to_string(&response)?.as_bytes())
                .await?;
            stdout.write_all(b"\n").await?;
        }
    }
    stdout.flush().await?;

    // Windows：已打开的句柄保活到父进程复制完成（父进程关闭 stdin 即 ack）。
    #[cfg(windows)]
    if opened_file.is_some() {
        use tokio::io::AsyncReadExt;

        let mut acknowledgement = Vec::new();
        stdin_lines
            .into_inner()
            .read_to_end(&mut acknowledgement)
            .await?;
    }
    drop(opened_file);
    Ok(())
}
