"""file_refresh_locks 文件锁 adapter 测试（跨进程 OAuth 防双刷的生产路径）。

语义对位内存实现（nova_async/tests/test_keyed_lock.py）：同 key 互斥、
异 key 不互阻、超时判死带名；外加文件实现的特有面——锁目录创建、
key 消毒。filelock 为 OS 级锁（per-fd），同进程两实例同样互斥。
"""

import asyncio

import pytest

from nova_harness.config.auth.refresh_locks import file_refresh_locks


def test_factory_creates_lock_dir(tmp_path):
    """锁目录在工厂创建时落地（filelock 的 acquire 要求父目录存在）。"""
    base = tmp_path / "auth-locks"
    file_refresh_locks(base)
    assert base.is_dir()


@pytest.mark.asyncio
async def test_same_key_excludes_until_release(tmp_path):
    """同 provider 第二把锁在持锁期间拿不到（互斥+超时判死）；释放后可得。"""
    factory = file_refresh_locks(tmp_path, timeout_s=0.3)
    first = factory("kimi-coding")
    second = factory("kimi-coding")
    await first.__aenter__()
    try:
        with pytest.raises(RuntimeError, match="kimi-coding"):
            await second.__aenter__()
    finally:
        await first.__aexit__(None, None, None)
    # 释放后立即可得
    await second.__aenter__()
    await second.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_different_keys_do_not_block(tmp_path):
    """不同 provider 的锁互不阻塞（锁粒度存在的理由）。"""
    factory = file_refresh_locks(tmp_path, timeout_s=0.3)
    held = factory("kimi-coding")
    await held.__aenter__()
    try:
        other = factory("volcengine")
        await asyncio.wait_for(other.__aenter__(), timeout=1.0)
        await other.__aexit__(None, None, None)
    finally:
        await held.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_key_sanitization(tmp_path):
    """provider id 的非安全字符落锁文件名时被消毒（不穿目录、不逃逸）。"""
    factory = file_refresh_locks(tmp_path)
    lock = factory("foo/bar baz")
    await lock.__aenter__()
    await lock.__aexit__(None, None, None)
    names = [p.name for p in tmp_path.iterdir()]
    assert names == ["foo_bar_baz.lock"]
