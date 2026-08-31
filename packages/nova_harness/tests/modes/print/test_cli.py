"""``nova-harness run`` CLI 参数透传测试。"""

from unittest.mock import AsyncMock, patch

from nova_harness.cli.main import main


def test_run_forwards_skill_and_prompt_template() -> None:
    """--skill / --prompt-template（可重复）应透传为 additional 路径参数。"""
    with patch(
        "nova_harness.modes.print.cli.run_print_mode", new=AsyncMock(return_value=0)
    ) as mock_run:
        result = main(
            [
                "run",
                "my-agent",
                "--task",
                "do it",
                "--skill",
                "/a/skills",
                "--skill",
                "/b/skills",
                "--prompt-template",
                "/p/prompts",
            ]
        )

    assert result == 0
    mock_run.assert_awaited_once()
    kwargs = mock_run.await_args.kwargs
    assert kwargs["additional_skill_paths"] == ["/a/skills", "/b/skills"]
    assert kwargs["additional_prompt_template_paths"] == ["/p/prompts"]


def test_run_defaults_to_empty_additional_paths() -> None:
    """未传参时 additional 路径为空列表（runner 内部归一为 None）。"""
    with patch(
        "nova_harness.modes.print.cli.run_print_mode", new=AsyncMock(return_value=0)
    ) as mock_run:
        result = main(["run", "my-agent", "--task", "do it"])

    assert result == 0
    kwargs = mock_run.await_args.kwargs
    assert kwargs["additional_skill_paths"] == []
    assert kwargs["additional_prompt_template_paths"] == []
