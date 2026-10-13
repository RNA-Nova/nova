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
    assert_eq!(result?.0, "other");
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
    assert_eq!(winner.await?.0, "shared");
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
        /*generation*/
        0, // 本用例不经 open_entry 注册条目，代际值不参与断言
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
    assert_eq!(winner.await?.0, "shared");
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
        anyhow::bail!(
            "the duplicate write stream open must be rejected before polling its file open"
        );
    };
    assert_eq!(error.kind(), io::ErrorKind::InvalidInput);
    assert!(!loser_polled.load(Ordering::SeqCst));

    // 胜者放行后正常落表
    send.send(File::from_std(tempfile::tempfile()?))
        .map_err(|_| anyhow::anyhow!("the pending open dropped its receiver"))?;
    assert_eq!(winner.await?.0, "shared");
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
    manager
        .open(
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
    manager
        .open(
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

/// 读流块长超线上 fs/readBlock 校验上限（FILE_READ_CHUNK_SIZE）时拆子读
/// 合并：字节拼接还原、seq 递增、仅末块 eof（回归：块长区间
/// (FILE_READ_CHUNK_SIZE, MAX_READ_STREAM_BLOCK_SIZE] 曾撞上 read_block 的
/// 校验上限而整流作废）。
#[test_case::test_case(2 * 1024 * 1024; "two_mib_blocks")]
#[test_case::test_case(4 * 1024 * 1024; "max_size_blocks")]
#[tokio::test]
async fn read_stream_oversized_block_is_split_into_wire_sized_reads(
    block_size: usize,
) -> Result<()> {
    use nova_exec_server_file_system::FILE_READ_CHUNK_SIZE;

    assert!(block_size > FILE_READ_CHUNK_SIZE);
    let manager = FileHandleManager::default();
    let data: Vec<u8> = (0..(2 * FILE_READ_CHUNK_SIZE + 77) as u32)
        .map(|index| (index % 251) as u8)
        .collect();
    let mut file = tempfile::tempfile()?;
    file.write_all(&data)?;
    let (_id, generation) = manager
        .open("big-read".to_string(), ready(Ok(File::from_std(file))))
        .await?;

    let mut emitted: Vec<(u64, Vec<u8>, bool)> = Vec::new();
    let (total_bytes, error, superseded) = super::stream_file_blocks(
        &manager,
        "big-read",
        generation,
        /*offset*/ 0,
        /*len*/ None,
        block_size,
        async |seq, bytes, eof| {
            emitted.push((seq, bytes, eof));
            true
        },
    )
    .await;

    assert_eq!(error, None);
    assert!(!superseded);
    assert_eq!(total_bytes, data.len() as u64);
    for (index, (seq, _, _)) in emitted.iter().enumerate() {
        assert_eq!(*seq, index as u64);
    }
    assert!(emitted.iter().all(|(_, bytes, _)| !bytes.is_empty()));
    assert!(!emitted[..emitted.len() - 1].iter().any(|(_, _, eof)| *eof));
    assert!(emitted.last().is_some_and(|(_, _, eof)| *eof));
    let reassembled: Vec<u8> = emitted
        .into_iter()
        .flat_map(|(_, bytes, _)| bytes)
        .collect();
    assert_eq!(reassembled, data);
    manager.close("big-read");
    Ok(())
}

/// done 响应返回时同名 handle_id 必须已可复用（写任务先摘句柄再交账——
/// 交账先于摘除会在 done 与任务收尾之间留出复用被误拒的窗口）。
#[tokio::test]
async fn write_stream_done_frees_the_handle_id_before_responding() -> Result<()> {
    let manager = FileHandleManager::default();
    manager
        .open_write_stream(
            "w-reuse".to_string(),
            ready(Ok(File::from_std(tempfile::tempfile()?))),
            /*offset*/ None,
        )
        .await?;
    manager
        .write_stream_chunk("w-reuse", 0, b"x".to_vec(), true)
        .await;
    let total = manager.finish_write_stream("w-reuse").await?;
    assert_eq!(total, 1);

    manager
        .open_write_stream(
            "w-reuse".to_string(),
            ready(Ok(File::from_std(tempfile::tempfile()?))),
            /*offset*/ None,
        )
        .await?;
    Ok(())
}

/// 代际守卫摘除：只摘匹配代际的条目——close→同名重开后旧任务的收尾
/// close_if_generation 不会误摘新句柄（S 级竞态回归：写流尾 close 摘除新条目）。
#[tokio::test]
async fn close_if_generation_only_removes_the_matching_generation() -> Result<()> {
    let manager = FileHandleManager::default();
    let (_id, first_generation) = manager
        .open(
            "gen".to_string(),
            ready(Ok(File::from_std(tempfile::tempfile()?))),
        )
        .await?;
    // 非本代：不动
    manager.close_if_generation("gen", first_generation + 1);
    assert_eq!(
        manager.handle_generation("gen"),
        Some(first_generation),
        "foreign generation must not remove the entry"
    );
    // 本代：摘除
    manager.close_if_generation("gen", first_generation);
    assert_eq!(manager.handle_generation("gen"), None);

    // 同名重开换代际；旧代际的收尾摘除不动新条目
    let (_id, second_generation) = manager
        .open(
            "gen".to_string(),
            ready(Ok(File::from_std(tempfile::tempfile()?))),
        )
        .await?;
    assert_ne!(first_generation, second_generation);
    manager.close_if_generation("gen", first_generation);
    assert_eq!(
        manager.handle_generation("gen"),
        Some(second_generation),
        "stale generation must not remove the reopened entry"
    );
    Ok(())
}

/// close→同名重开后，旧写任务的收尾 close 不得摘除新句柄（S 级竞态回归）。
#[tokio::test]
async fn write_stream_task_close_after_reopen_keeps_the_new_entry() -> Result<()> {
    let manager = FileHandleManager::default();
    manager
        .open_write_stream(
            "w-race".to_string(),
            ready(Ok(File::from_std(tempfile::tempfile()?))),
            /*offset*/ None,
        )
        .await?;
    // 客户端 close 摘旧条目（写任务 channel 对端掉落，旧任务将被唤醒收尾），
    // 随即同名重开普通句柄——无代际防护时旧任务的尾 close 会摘掉这个新条目。
    manager.close("w-race");
    manager
        .open(
            "w-race".to_string(),
            ready(Ok(File::from_std(tempfile::tempfile()?))),
        )
        .await?;
    // 让旧写任务充分调度（recv=None 即刻就绪，50ms 裕量足够其收尾执行）
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    assert_eq!(
        manager.open_handle_count(),
        1,
        "the reopened entry must survive the stale writer task's teardown"
    );
    manager
        .read_block("w-race", /*offset*/ 0, /*len*/ 1)
        .await?;
    Ok(())
}

/// close→同名重开后，旧读流任务静默收尾：不串读新文件、不再发射、不摘新句柄
/// （S 级竞态回归：跨文件串流 + 误摘新句柄）。
#[tokio::test]
async fn read_stream_superseded_stops_quietly_without_touching_the_new_entry() -> Result<()> {
    let manager = FileHandleManager::default();
    let mut old_file = tempfile::tempfile()?;
    old_file.write_all(&vec![b'a'; 256 * 1024])?;
    old_file.flush()?;
    let (_id, generation) = manager
        .open(
            "r-race".to_string(),
            ready(Ok(File::from_std(old_file.try_clone()?))),
        )
        .await?;

    // 首块发射期间完成 close→同名重开（emit 闭包内驱动，时序确定性）
    let driver = manager.clone();
    let mut reopened = false;
    let (total_bytes, error, superseded) = super::stream_file_blocks(
        &manager,
        "r-race",
        generation,
        /*offset*/ 0,
        /*len*/ None,
        /*block_size*/ 64 * 1024,
        async move |_seq, _bytes, _eof| {
            if !reopened {
                reopened = true;
                driver.close("r-race");
                let mut new_file = tempfile::tempfile().expect("new file");
                new_file.write_all(b"b").expect("write new file");
                new_file.flush().expect("flush new file");
                driver
                    .open(
                        "r-race".to_string(),
                        ready(Ok(File::from_std(new_file.try_clone().expect("clone")))),
                    )
                    .await
                    .expect("reopen under the same id");
            }
            true
        },
    )
    .await;

    assert!(
        superseded,
        "the stale stream must detect the id reuse and stop quietly"
    );
    assert_eq!(error, None);
    assert_eq!(total_bytes, (64 * 1024) as u64);
    // 新条目未被旧任务误摘，且读到的是新文件内容（无跨文件串流）
    let block = manager
        .read_block("r-race", /*offset*/ 0, /*len*/ 1)
        .await?;
    assert_eq!(block.bytes, b"b");
    Ok(())
}

/// 纯摘除（close 无重开）保持既有语义：流以 unknown handle 错误收尾、
/// superseded = false（close_interrupts_stream 的 done 带 error 语义不落）。
#[tokio::test]
async fn read_stream_plain_close_still_finishes_with_unknown_handle_error() -> Result<()> {
    let manager = FileHandleManager::default();
    let mut old_file = tempfile::tempfile()?;
    old_file.write_all(&vec![b'c'; 256 * 1024])?;
    old_file.flush()?;
    let (_id, generation) = manager
        .open(
            "r-close".to_string(),
            ready(Ok(File::from_std(old_file.try_clone()?))),
        )
        .await?;

    let driver = manager.clone();
    let mut closed = false;
    let (_total_bytes, error, superseded) = super::stream_file_blocks(
        &manager,
        "r-close",
        generation,
        /*offset*/ 0,
        /*len*/ None,
        /*block_size*/ 64 * 1024,
        async move |_seq, _bytes, _eof| {
            if !closed {
                closed = true;
                driver.close("r-close");
            }
            true
        },
    )
    .await;

    assert!(!superseded);
    let error = error.expect("plain close must surface an error");
    assert!(
        error.contains("unknown file handle"),
        "unexpected error: {error}"
    );
    Ok(())
}
