"""core/utils/child_process.py 单元测试。"""

import subprocess
import time

from nova_harness.core.utils.child_process import (
    _tracked_detached_child_pids,
    kill_process_tree,
    kill_tracked_detached_children,
    track_detached_child_pid,
    untrack_detached_child_pid,
)


def teardown_function():
    """每个测试后清空跟踪表，避免交叉污染。"""
    _tracked_detached_child_pids.clear()


def test_track_and_untrack():
    track_detached_child_pid(12345)
    assert 12345 in _tracked_detached_child_pids
    untrack_detached_child_pid(12345)
    assert 12345 not in _tracked_detached_child_pids
    # 重复 untrack 不报错
    untrack_detached_child_pid(12345)


def test_kill_tracked_detached_children():
    """被跟踪的 detached 进程在清场时被 kill，跟踪表清空。"""
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    track_detached_child_pid(proc.pid)
    try:
        kill_tracked_detached_children()
        proc.wait(timeout=5)
        assert proc.returncode is not None
        assert not _tracked_detached_child_pids
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_kill_tracked_detached_children_with_dead_pid():
    """跟踪表里混入已死 pid 时清场不报错。"""
    _tracked_detached_child_pids.add(999999)
    kill_tracked_detached_children()
    assert not _tracked_detached_child_pids


def test_kill_process_tree_kills_group():
    """kill_process_tree 结束整棵进程树（组长的子进程一并终止）。"""
    proc = subprocess.Popen(
        ["bash", "-c", "sleep 60 & sleep 60"],
        start_new_session=True,
    )
    time.sleep(0.2)
    kill_process_tree(proc.pid)
    proc.wait(timeout=5)
    assert proc.returncode is not None


def test_kill_process_tree_dead_pid_no_error():
    """对已不存在的 pid 调用不报错。"""
    kill_process_tree(999999)
