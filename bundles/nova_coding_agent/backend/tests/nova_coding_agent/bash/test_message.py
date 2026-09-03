"""BashExecutionMessage 的 to_context_text 格式测试。"""

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
