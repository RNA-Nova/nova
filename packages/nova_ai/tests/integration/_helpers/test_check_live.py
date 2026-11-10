"""live 巡检 CLI 的密封启动测试（不执行真实 provider 请求）。"""

import os
import subprocess
import sys
from pathlib import Path

_NOVA_AI_ROOT = Path(__file__).resolve().parents[3]


def _run(*args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "tests.integration.check_live", *args],
        cwd=_NOVA_AI_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_check_live_help_imports_from_repository_root():
    result = _run("--help")
    assert result.returncode == 0, result.stderr
    assert "--provider" in result.stdout


def test_check_live_rejects_unknown_provider_before_manager_setup():
    result = _run("--provider", "definitely-not-a-provider")
    assert result.returncode == 2
    assert "未知 provider" in result.stderr


def test_check_live_empty_credentials_exit_cleanly(tmp_path):
    env = {
        key: value
        for key, value in os.environ.items()
        if not any(
            marker in key.upper()
            for marker in ("API_KEY", "TOKEN", "SECRET", "CREDENTIAL")
        )
    }
    env["HOME"] = str(tmp_path)
    result = _run(env=env)
    assert result.returncode == 2, result.stderr
    assert "没有实际执行任何巡检用例" in result.stderr
