"""BashExecutionMessage 的 to_context_text 格式与 to_item 呈现桥测试。"""

from nova_server.types.items import ItemStatus

from nova_coding_agent.bash.message import BashExecutionMessage


def _msg(**overrides) -> BashExecutionMessage:
    base = dict(
        command="ls",
        output="a\nb",
        exit_code=0,
        cancelled=False,
        truncated=False,
        timestamp=1700000000000,
    )
    base.update(overrides)
    return BashExecutionMessage(**base)


def test_to_context_text_with_output():
    text = _msg().to_context_text()
    assert "Ran `ls`" in text
    assert "```\na\nb\n```" in text


def test_to_context_text_no_output():
    text = _msg(output="").to_context_text()
    assert "(no output)" in text


def test_to_context_text_cancelled():
    text = _msg(
        command="sleep 10", output="", exit_code=None, cancelled=True
    ).to_context_text()
    assert "(command cancelled)" in text


def test_to_context_text_nonzero_exit():
    text = _msg(command="false", output="", exit_code=1).to_context_text()
    assert "Command exited with code 1" in text


def test_to_context_text_truncated():
    text = _msg(
        command="cat big.log",
        output="...",
        truncated=True,
        full_output_path="/tmp/big.log",
    ).to_context_text()
    assert "[Output truncated. Full output: /tmp/big.log]" in text


def test_role_discriminator():
    assert _msg().role == "bashExecution"
    assert _msg().model_dump(mode="json")["role"] == "bashExecution"


# ---------------------------------------------------------------------------
# to_item 呈现桥（实时定稿与恢复读共用）
# ---------------------------------------------------------------------------


def test_to_item_success_maps_done():
    item = _msg(item_id="b1").to_item()
    assert item.id == "b1"
    assert item.type == "bashExecution"
    assert item.status is ItemStatus.DONE
    assert item.source == "user"
    assert item.command == "ls"
    assert item.output == "a\nb"
    assert item.exit_code == 0
    # 线上形态：camelCase + Enum 取 value + 包字段齐全
    dumped = item.dump_wire()
    assert dumped["status"] == "done"
    assert dumped["exitCode"] == 0
    assert dumped["excludeFromContext"] is False


def test_to_item_cancelled_and_failed():
    assert _msg(exit_code=None, cancelled=True).to_item().status is ItemStatus.CANCELLED
    assert _msg(exit_code=2).to_item().status is ItemStatus.FAILED
    # 退出码缺失且未取消（扩展拦截路径的自构结果）：视作正常结束
    assert _msg(exit_code=None).to_item().status is ItemStatus.DONE


def test_message_jsonl_roundtrip_preserves_item_id():
    """item_id 随 JSONL 落盘——恢复路径经 to_item 产出同 id 的 item（同形）。"""
    msg = _msg(item_id="b7")
    restored = BashExecutionMessage.model_validate(msg.model_dump(mode="json"))
    assert restored.item_id == "b7"
    assert restored.to_item().id == "b7"
