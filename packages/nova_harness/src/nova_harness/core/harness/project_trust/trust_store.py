"""Project Trust 存储管理。

保存位置：``~/.nova/agent/trust.json``。
"""

import json
from pathlib import Path
from typing import Dict, List, Optional

from filelock import FileLock
from nova_harness.core.config.defaults import TRUST_FILE_NAME
from nova_harness.core.types.project_trust import ProjectTrustUpdate


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
        """读取 trust 文件；损坏时抛错（对齐 TS readTrustFile）。

        trust 是安全状态：静默重置会把用户曾经的 "do not trust" 决策抹掉、
        落回 default/ask 分支，形成 trust 降级通道。抛错让用户立即感知，
        删除损坏文件即可恢复。
        """
        if not self._path.exists():
            return {}
        try:
            with open(self._path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Failed to read trust store {self._path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"Invalid trust store {self._path}: expected an object")
        result: Dict[str, Optional[bool]] = {}
        for key, value in data.items():
            if value is True or value is False or value is None:
                result[key] = value
            else:
                raise ValueError(
                    f"Invalid trust store {self._path}: value for {key!r} "
                    "must be true, false, or null"
                )
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
        """查找 cwd 或其最近父目录的信任决策。

        null 条目是"显式清除"标记：跳过并继续向上查找（对齐 TS
        ``findNearestTrustEntry``）。
        """
        data = self._with_lock(self._read)
        current = Path(cwd).resolve()
        while True:
            key = str(current)
            value = data.get(key)
            if value is True or value is False:
                return value
            parent = current.parent
            if parent == current:
                return None
            current = parent

    def set(self, path: str, decision: Optional[bool]) -> None:
        """设置单条信任记录（decision=None 删除，对齐 TS set→setMany 包装）。"""
        self.set_many([ProjectTrustUpdate(path=path, decision=decision)])

    def set_many(self, updates: List[ProjectTrustUpdate]) -> None:
        """批量设置信任记录；decision 为 None 时删除该路径的记录。"""

        def _do():
            data = self._read()
            for update in updates:
                key = str(Path(update.path).resolve())
                if update.decision is None:
                    data.pop(key, None)
                else:
                    data[key] = bool(update.decision)
            self._write(data)

        self._with_lock(_do)
