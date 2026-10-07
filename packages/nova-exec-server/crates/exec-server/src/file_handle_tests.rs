//! 句柄容量/槽位语义测试（对位 codex d25c114d49 `file_handle_tests.rs`）：
//! 不依赖 I/O 时序，直接驱动 open future 的 poll 状态验证槽位清理与推进。

use std::future::pending;
use std::future::ready;
use std::io;
use std::io::Write;
use std::task::Poll;

use anyhow::Result;
use pretty_assertions::assert_eq;
use tokio::fs::File;
use tokio::sync::oneshot;

use super::FileHandleManager;
use super::FileReadBlock;
use super::MAX_OPEN_FILES;

/// A stalled open must let unrelated handles open and read without waiting for it.
/// （卡住的打开不得阻塞无关句柄的打开与读取）
#[tokio::test]
async fn stalled_open_does_not_block_other_handles() -> Result<()> {
    let manager = FileHandleManager::default();
    let mut stalled = Box::pin(manager.open("stalled".to_string(), pending()));
    assert!(futures::poll!(stalled.as_mut()).is_pending());

    let mut other = Box::pin(manager.open(
        "other".to_string(),
        ready(Ok(File::from_std(tempfile::tempfile()?))),
    ));
    let Poll::Ready(result) = futures::poll!(other.as_mut()) else {
        anyhow::bail!("an unrelated open was blocked by the stalled open");
    };
    assert_eq!(result?, "other");
    manager.read_block("other", /*offset*/ 0, /*len*/ 1).await?;
    Ok(())
}

/// Pending opens consume capacity before polling another file open.
/// （在飞行打开先占槽：128 个 pending 打开后第 129 个必须即拒）
#[tokio::test]
async fn pending_opens_reserve_capacity() -> Result<()> {
    let manager = FileHandleManager::default();
    let mut opens = Vec::new();
    for index in 0..MAX_OPEN_FILES {
        let mut open = Box::pin(manager.open(index.to_string(), pending()));
        assert!(futures::poll!(open.as_mut()).is_pending());
        opens.push(open);
    }
    let mut rejected = Box::pin(manager.open("overflow".to_string(), pending()));
    let Poll::Ready(Err(error)) = futures::poll!(rejected.as_mut()) else {
        anyhow::bail!("admission must fail before polling the file open");
    };
    assert_eq!(error.kind(), io::ErrorKind::InvalidInput);
    Ok(())
}

/// Failed and cancelled opens must return their permits for reuse.
/// （失败/取消的打开必须归还槽位——槽位可无限复用）
#[test_case::test_case(true; "cancelled")]
#[test_case::test_case(false; "failed")]
#[tokio::test]
async fn unfinished_opens_release_capacity(cancelled: bool) -> Result<()> {
    let manager = FileHandleManager::default();
    for _ in 0..=MAX_OPEN_FILES {
        let mut open = Box::pin(manager.open("reused".to_string(), async {
            if cancelled {
                pending::<()>().await;
            }
            Err(io::Error::other("open failed"))
        }));
        let result = futures::poll!(open.as_mut());
        if cancelled {
            assert!(result.is_pending());
        } else {
            let Poll::Ready(Err(error)) = result else {
                anyhow::bail!("the open error must reach the caller");
            };
            assert_eq!(error.kind(), io::ErrorKind::Other);
        }
        drop(open);
    }
    Ok(())
}

/// Closing an entry frees capacity even while an operation retains its file descriptor.
/// （关闭条目即释放槽位，即使读操作仍持有该文件的 fd）
#[tokio::test]
async fn close_releases_capacity_while_the_file_is_still_in_use() -> Result<()> {
    let manager = FileHandleManager::default();
    let file = tempfile::tempfile()?;
    for index in 0..MAX_OPEN_FILES {
        manager
            .open(
                index.to_string(),
                ready(Ok(File::from_std(file.try_clone()?))),
            )
            .await?;
    }
    let retained = manager.get("0")?;
    manager.close("0");
    manager
        .open("next".to_string(), ready(Ok(File::from_std(file))))
        .await?;
    drop(retained);
    Ok(())
}

/// Concurrent opens of one ID must reject the loser without changing the winning file.
/// （同名并发打开：后到者拒绝且不替换先到句柄的文件）
#[tokio::test]
async fn concurrent_opens_reject_duplicate_ids_without_replacing_the_file() -> Result<()> {
    let manager = FileHandleManager::default();
    let (send, receive) = oneshot::channel();
    let mut late = Box::pin(manager.open("shared".to_string(), async {
        receive.await.map_err(io::Error::other)
    }));
    assert!(futures::poll!(late.as_mut()).is_pending());

    let mut file = tempfile::tempfile()?;
    file.write_all(b"first")?;
    manager
        .open("shared".to_string(), ready(Ok(File::from_std(file))))
        .await?;
    send.send(File::from_std(tempfile::tempfile()?))
        .map_err(|_| anyhow::anyhow!("the pending open dropped its receiver"))?;

    let error = late.await.expect_err("the duplicate open must fail");
    assert_eq!(error.kind(), io::ErrorKind::InvalidInput);
    assert_eq!(
        manager
            .read_block("shared", /*offset*/ 0, /*len*/ 6)
            .await?,
        FileReadBlock {
            bytes: b"first".to_vec(),
            eof: true,
        }
    );
    Ok(())
}

/// 断连/close 摘条目后写任务自然结束（channel 对端全掉 → recv=None → 任务退出），
/// 已落盘内容留在盘上（与读流的断连停止镜像；nova 自有通道）。
#[tokio::test]
async fn write_stream_task_exits_when_the_channel_drops() -> Result<()> {
    use std::sync::Arc;

    use super::WriteStreamInput;

    let manager = FileHandleManager::default();
    let native = tempfile::NamedTempFile::new()?;
    let path = native.path().to_path_buf();
    let file = Arc::new(native.reopen()?);
    let (tx, rx) = tokio::sync::mpsc::unbounded_channel();
    let task = tokio::spawn(super::run_write_stream_task(
        manager.clone(),
        "w-drop".to_string(),
        file,
        rx,
        /*offset*/ 0,
    ));

    tx.send(WriteStreamInput::Chunk {
        seq: 0,
        bytes: b"ab".to_vec(),
        eof: false,
    })
    .expect("chunk should send");
    // 断连/close 摘条目 → tx 全掉：chunk 先于关闭到达 channel，任务先落盘再退出
    drop(tx);
    tokio::time::timeout(std::time::Duration::from_secs(5), task)
        .await
        .expect("write stream task should end when its channel drops")
        .expect("write stream task should not panic");

    // 任务退出时写入已落定（同 channel 顺序：chunk 先于断开被处理）
    assert_eq!(std::fs::read(&path)?, b"ab");
    Ok(())
}
