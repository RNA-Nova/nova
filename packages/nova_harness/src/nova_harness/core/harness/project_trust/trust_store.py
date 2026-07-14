"""Project Trust 存储管理。

保存位置：``~/.nova/agent/trust.json``。
"""

import json
from pathlib import Path
from typing import Dict, Optional

from filelock import FileLock

from nova_harness.core.config.defaults import TRUST_FILE_NAME


class ProjectTrustStore:
    """管理项目信任记录的文件存储。"""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._lock_path = Path(str(path) + ".lock")

    @classmethod
    def for_agent_dir(cls, agent_dir: str) -> "ProjectTrustStore":
        """根据 agent 目录构造 trust store。"""
        path = Path(agent_dir) / TRUST_FILE_NAME
        return cls(str(path))

    def _read(self) -> Dict[str, Optional[bool]]:
        if not self._path.exists():
            return {}
        try:
            with open(self._path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}
        result: Dict[str, Optional[bool]] = {}
        for key, value in data.items():
            if value is True or value is False or value is None:
                result[key] = value
        return result

    def _write(self, data: Dict[str, Optional[bool]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        sorted_data = {k: data[k] for k in sorted(data.keys())}
        with open(self._path, "w", encoding="utf-8") as fp:
            json.dump(sorted_data, fp, ensure_ascii=False, indent=2)
            fp.write("\n")

    def _with_lock(self, fn):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self._lock_path)):
            return fn()

    def get(self, cwd: str) -> Optional[bool]:
        """查找 cwd 或其最近父目录的信任决策。"""
        data = self._with_lock(self._read)
        current = Path(cwd).resolve()
        while True:
            key = str(current)
            if key in data:
                return data[key]
            parent = current.parent
            if parent == current:
                return None
            current = parent

    def set(self, path: str, decision: Optional[bool]) -> None:
        """设置单条信任记录。"""

        def _do():
            data = self._read()
            data[str(Path(path).resolve())] = decision
            self._write(data)

        self._with_lock(_do)

    def set_many(self, updates: list) -> None:
        """批量设置信任记录。"""

        def _do():
            data = self._read()
            for update in updates:
                path = getattr(update, "path", None) or update.get("path")
                decision = getattr(update, "decision", None)
                if decision is None:
                    data.pop(str(Path(path).resolve()), None)
                else:
                    data[str(Path(path).resolve())] = bool(decision)
            self._write(data)

        self._with_lock(_do)
