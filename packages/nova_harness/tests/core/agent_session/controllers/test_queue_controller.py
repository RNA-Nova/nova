"""
QueueController 单元测试。

验证 steering / follow-up 队列的追加、清空与事件发射。
"""

import pytest
from nova_ai import ImageContent

from nova_harness.core.types.events import QueueUpdateEvent


@pytest.fixture
def queue_session(make_agent_session):
    """构造一个用于测试 QueueController 的 session。"""
    return make_agent_session()


def test_emit_update(queue_session):
    """emit_update 应发射当前队列状态的副本。"""
    queue_session._steering_messages.append("s1")
    queue_session._follow_up_messages.append("f1")
    events = []
    queue_session.subscribe(events.append)

    queue_session._queue.emit_update()

    ev = events[0]
    assert isinstance(ev, QueueUpdateEvent)
    assert ev.steering == ["s1"]
    assert ev.follow_up == ["f1"]
    # 确保返回的是副本
    ev.steering.append("x")
    assert queue_session._steering_messages == ["s1"]


@pytest.mark.asyncio
async def test_steer_appends_message_and_calls_agent(queue_session):
    """steer 应追加文本、发射更新并调用 agent.steer。"""
    await queue_session._queue.steer("keep it short")

    assert queue_session._steering_messages == ["keep it short"]
    queue_session.agent.steer.assert_called_once()
    msg = queue_session.agent.steer.call_args[0][0]
    assert msg.role == "user"
    assert len(msg.content) == 1
    assert msg.content[0].type == "text"
    assert msg.content[0].text == "keep it short"


@pytest.mark.asyncio
async def test_steer_with_images(queue_session):
    """steer 应把图片附加到 UserMessage content。"""
    img = ImageContent(type="image", url="http://example.com/x.png")
    await queue_session._queue.steer("look", images=[img])

    msg = queue_session.agent.steer.call_args[0][0]
    assert len(msg.content) == 2
    assert msg.content[0].type == "text"
    assert msg.content[1] is img


@pytest.mark.asyncio
async def test_follow_up_appends_message_and_calls_agent(queue_session):
    """follow_up 应追加到 follow_up 队列并调用 agent.follow_up。"""
    await queue_session._queue.follow_up("continue")

    assert queue_session._follow_up_messages == ["continue"]
    queue_session.agent.follow_up.assert_called_once()
    msg = queue_session.agent.follow_up.call_args[0][0]
    assert msg.content[0].text == "continue"


@pytest.mark.asyncio
async def test_follow_up_with_images(queue_session):
    """follow_up 也应支持图片。"""
    img = ImageContent(type="image", url="data:image/png;base64,abc")
    await queue_session._queue.follow_up("explain", images=[img])

    msg = queue_session.agent.follow_up.call_args[0][0]
    assert msg.content[1] is img


def test_clear_returns_previous_messages(queue_session):
    """clear 应返回之前的内容并清空队列。"""
    queue_session._steering_messages = ["s1"]
    queue_session._follow_up_messages = ["f1", "f2"]
    events = []
    queue_session.subscribe(events.append)

    result = queue_session._queue.clear()

    assert result == {"steering": ["s1"], "follow_up": ["f1", "f2"]}
    assert queue_session._steering_messages == []
    assert queue_session._follow_up_messages == []
    queue_session.agent.clear_all_queues.assert_called_once()
    assert any(isinstance(e, QueueUpdateEvent) for e in events)


def test_get_steering_read_only(queue_session):
    """get_steering 返回队列副本。"""
    queue_session._steering_messages = ["a", "b"]
    result = queue_session._queue.get_steering()
    result.append("c")
    assert queue_session._steering_messages == ["a", "b"]


def test_get_follow_up_read_only(queue_session):
    """get_follow_up 返回队列副本。"""
    queue_session._follow_up_messages = ["x"]
    result = queue_session._queue.get_follow_up()
    result.clear()
    assert queue_session._follow_up_messages == ["x"]
