"""nova-gateway CLI 的真实日志装配与多出口过滤。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from nova_gateway.cli import main


def test_serve_configures_stdout_file_and_target_levels(
    tmp_path: Path, monkeypatch, capsys
):
    config = tmp_path / "gateway.yaml"
    config.write_text("{}", encoding="utf-8")
    log_file = tmp_path / "gateway.jsonl"

    def fake_run(app: Any, **kwargs: Any) -> None:
        logging.getLogger("tests.cli").debug("tests.debug")
        for name in ("httpx", "httpx2", "httpcore", "openai", "anthropic"):
            logging.getLogger(name).info(
                "HTTP Request: https://example.test?key=secret"
            )
            logging.getLogger(name).debug(
                "Request options: {'content': 'private-body'}"
            )

    monkeypatch.setattr("nova_gateway.cli.uvicorn.run", fake_run)
    result = main(
        [
            "serve",
            "--config",
            str(config),
            "--token",
            "secret",
            "--log-level",
            "DEBUG",
            "--log-format",
            "console",
            "--log-file",
            str(log_file),
            "--log-target",
            "tests.cli=DEBUG",
            "--log-target",
            "httpx=DEBUG",
        ]
    )

    assert result == 0
    stdout = capsys.readouterr().out
    assert "tests.debug" in stdout
    records = [json.loads(line) for line in log_file.read_text("utf-8").splitlines()]
    assert [record["event"] for record in records] == ["tests.debug"]
    assert "key=secret" not in stdout
    assert "private-body" not in stdout
