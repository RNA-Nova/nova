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
use super::WRITE_STREAM_QUEUE_CAPACITY;

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
/// （同名并发打开：先到者持在飞预约，后到者在碰文件前即拒——不替换先到句柄）
#[tokio::test]
async fn concurrent_opens_reject_duplicate_ids_without_replacing_the_file() -> Result<()> {
    let manager = FileHandleManager::default();
    let (send, receive) = oneshot::channel();
    let mut winner = Box::pin(manager.open("shared".to_string(), async {
        receive.await.map_err(io::Error::other)
    }));
    assert!(futures::poll!(winner.as_mut()).is_pending());

    // 后到同名：在飞预约即拒（重复 ID 在碰文件前拒绝，nova 自有预约层）
    let mut late_file = tempfile::tempfile()?;
    late_file.write_all(b"late")?;
    let error = manager
        .open("shared".to_string(), ready(Ok(File::from_std(late_file))))
        .await
        .expect_err("the duplicate open must fail");
    assert_eq!(error.kind(), io::ErrorKind::InvalidInput);

    // 先到者放行后正常落表，内容不被后到者替换
    let mut winning_file = tempfile::tempfile()?;
    winning_file.write_all(b"first")?;
    send.send(File::from_std(winning_file))
        .map_err(|_| anyhow::anyhow!("the pending open dropped its receiver"))?;
    assert_eq!(winner.await?, "shared");
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
    let (tx, rx) = tokio::sync::mpsc::channel(WRITE_STREAM_QUEUE_CAPACITY);
    let task = tokio::spawn(super::run_write_stream_task(
        manager.clone(),
        "w-drop".to_string(),
        file,
        rx,
        Default::default(), // 背压 latch：本用例不触发
        /*offset*/ 0,
    ));

    tx.send(WriteStreamInput::Chunk {
        seq: 0,
        bytes: b"ab".to_vec(),
        eof: false,
    })
    .await
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

/// 并发同名打开：在飞预约让败者在碰文件前被拒——其 open_file 从未被 poll
/// （nova 自有预约层；codex 上游仅靠落表复查，败者的 Replace 截断副作用
/// 已经发生）。胜者独占开门，全程只执行一次 open_file。
#[tokio::test]
async fn duplicate_in_flight_open_is_rejected_before_polling_open_file() -> Result<()> {
    use std::sync::Arc;
    use std::sync::atomic::AtomicBool;
    use std::sync::atomic::AtomicUsize;
    use std::sync::atomic::Ordering;

    let manager = FileHandleManager::default();
    let winner_opens = Arc::new(AtomicUsize::new(0));
    let (send, receive) = oneshot::channel();
    let winner_opens_in_future = Arc::clone(&winner_opens);
    let mut winner = Box::pin(manager.open("shared".to_string(), async move {
        winner_opens_in_future.fetch_add(1, Ordering::SeqCst);
        receive.await.map_err(io::Error::other)
    }));
    assert!(futures::poll!(winner.as_mut()).is_pending());
    assert_eq!(winner_opens.load(Ordering::SeqCst), 1);

    // 同名败者：即拒（重复错误），且其 open_file 从未被 poll——截断副作用不会发生
    let loser_polled = Arc::new(AtomicBool::new(false));
    let loser_polled_in_future = Arc::clone(&loser_polled);
    let mut loser = Box::pin(manager.open(
        "shared".to_string(),
        std::future::poll_fn(move |_| -> Poll<io::Result<File>> {
            loser_polled_in_future.store(true, Ordering::SeqCst);
            Poll::Pending
        }),
    ));
    let Poll::Ready(Err(error)) = futures::poll!(loser.as_mut()) else {
        anyhow::bail!("the duplicate open must be rejected before polling its file open");
    };
    assert_eq!(error.kind(), io::ErrorKind::InvalidInput);
    assert!(!loser_polled.load(Ordering::SeqCst));

    // 胜者放行后正常落表，全程只开了一次文件
    send.send(File::from_std(tempfile::tempfile()?))
        .map_err(|_| anyhow::anyhow!("the pending open dropped its receiver"))?;
    assert_eq!(winner.await?, "shared");
    assert_eq!(winner_opens.load(Ordering::SeqCst), 1);
    Ok(())
}

/// open_write_stream 同构：并发同名写流打开，败者在开门前被拒且其 open_file
/// 从未被 poll（写流 Replace=截断——败者的 open_file 若执行会砸掉胜者的文件）。
#[tokio::test]
async fn duplicate_in_flight_write_stream_open_is_rejected_before_polling_open_file() -> Result<()>
{
    use std::sync::Arc;
    use std::sync::atomic::AtomicBool;
    use std::sync::atomic::Ordering;

    let manager = FileHandleManager::default();
    let (send, receive) = oneshot::channel();
    let mut winner = Box::pin(manager.open_write_stream(
        "shared".to_string(),
        async move { receive.await.map_err(io::Error::other) },
        None,
    ));
    assert!(futures::poll!(winner.as_mut()).is_pending());

    // 同名败者：即拒（重复错误），且其 open_file 从未被 poll
    let loser_polled = Arc::new(AtomicBool::new(false));
    let loser_polled_in_future = Arc::clone(&loser_polled);
    let mut loser = Box::pin(manager.open_write_stream(
        "shared".to_string(),
        std::future::poll_fn(move |_| -> Poll<io::Result<File>> {
            loser_polled_in_future.store(true, Ordering::SeqCst);
            Poll::Pending
        }),
        None,
    ));
    let Poll::Ready(Err(error)) = futures::poll!(loser.as_mut()) else {
        anyhow::bail!("the duplicate write stream open must be rejected before polling its file open");
    };
    assert_eq!(error.kind(), io::ErrorKind::InvalidInput);
    assert!(!loser_polled.load(Ordering::SeqCst));

    // 胜者放行后正常落表
    send.send(File::from_std(tempfile::tempfile()?))
        .map_err(|_| anyhow::anyhow!("the pending open dropped its receiver"))?;
    assert_eq!(winner.await?, "shared");
    Ok(())
}

/// open_file 失败必须释放在飞预约（nova 自有预约守卫）：同名可立即重试。
#[tokio::test]
async fn failed_open_releases_the_in_flight_reservation() -> Result<()> {
    let manager = FileHandleManager::default();
    let error = manager
        .open(
            "retry".to_string(),
            ready(Err(io::Error::other("open failed"))),
        )
        .await
        .expect_err("the open error must reach the caller");
    assert_eq!(error.kind(), io::ErrorKind::Other);

    // 预约已随失败释放：同名重试正常开门落表
    manager.open(
        "retry".to_string(),
        ready(Ok(File::from_std(tempfile::tempfile()?))),
    )
    .await?;
    Ok(())
}

/// open future 被取消（poll 后 drop）同样必须释放在飞预约：同名可立即重试。
#[tokio::test]
async fn cancelled_open_releases_the_in_flight_reservation() -> Result<()> {
    let manager = FileHandleManager::default();
    let mut cancelled = Box::pin(manager.open("retry".to_string(), pending()));
    assert!(futures::poll!(cancelled.as_mut()).is_pending());
    drop(cancelled);

    // 预约已随取消释放：同名重试正常开门落表
    manager.open(
        "retry".to_string(),
        ready(Ok(File::from_std(tempfile::tempfile()?))),
    )
    .await?;
    Ok(())
}

/// 写流背压（bounded channel，容量 16）：队列灌满后第 17 块记终态失败
/// latch——chunk 通知仍恒成功（无回执），done 回报该错误，队列中的块全部
/// 静默排空（文件不被写入）。与协议违规同一语义。
#[tokio::test]
async fn write_stream_queue_full_latches_failure_reported_at_done() -> Result<()> {
    let manager = FileHandleManager::default();
    let native = tempfile::NamedTempFile::new()?;
    let path = native.path().to_path_buf();
    manager
        .open_write_stream(
            "w-full".to_string(),
            ready(Ok(File::from_std(native.reopen()?))),
            None,
        )
        .await?;

    // 手动 poll 投递（不驱动运行时，写任务不会被调度）：16 块灌满队列。
    // 确定性依据：写任务在 open_write_stream 返回前才 spawn，此后无任何
    // 让出点；futures::poll! 以内联 waker 轮询，写任务自始至终未被 poll。
    for seq in 0..WRITE_STREAM_QUEUE_CAPACITY as u64 {
        let mut offer = Box::pin(manager.write_stream_chunk("w-full", seq, b"x".to_vec(), false));
        let Poll::Ready(()) = futures::poll!(offer.as_mut()) else {
            anyhow::bail!("offering a chunk must complete without yielding");
        };
    }
    // 第 17 块：队列满 → 背压 latch 置位（通知无回执，投递仍即成功）
    let mut overflow = Box::pin(manager.write_stream_chunk(
        "w-full",
        WRITE_STREAM_QUEUE_CAPACITY as u64,
        b"x".to_vec(),
        false,
    ));
    let Poll::Ready(()) = futures::poll!(overflow.as_mut()) else {
        anyhow::bail!("offering a chunk must complete without yielding");
    };

    // done 回报 latch 错误；队列中的块全部静默排空（文件保持空）
    let error = manager
        .finish_write_stream("w-full")
        .await
        .expect_err("the latched backpressure failure must surface at done");
    assert_eq!(error.kind(), io::ErrorKind::ResourceBusy);
    assert!(error.to_string().contains("queue full"));
    assert_eq!(std::fs::read(&path)?, b"");
    Ok(())
}

/// 正常小流不受背压影响：块按序落盘，done 回报全量字节数（游标终点）。
#[tokio::test]
async fn write_stream_small_stream_is_unaffected_by_backpressure() -> Result<()> {
    let manager = FileHandleManager::default();
    let native = tempfile::NamedTempFile::new()?;
    let path = native.path().to_path_buf();
    manager
        .open_write_stream(
            "w-small".to_string(),
            ready(Ok(File::from_std(native.reopen()?))),
            None,
        )
        .await?;
    manager
        .write_stream_chunk("w-small", 0, b"hello ".to_vec(), false)
        .await;
    manager
        .write_stream_chunk("w-small", 1, b"world".to_vec(), true)
        .await;
    let total = manager.finish_write_stream("w-small").await?;
    assert_eq!(total, 11);
    assert_eq!(std::fs::read(&path)?, b"hello world");
    Ok(())
}
