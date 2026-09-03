"""目录布局迁移（前后端分治 §9——user/project 两级目录的后端半区归位）。

旧版把后端散养资源直接散在 user/project 根下（``<base>/extensions`` 等），
现统一归 ``<base>/backend/`` 半区。会话服务装配（``AgentSessionServices.create``）
时检测旧位目录，存在即整体搬迁：

- **mv 语义**：只搬不删，旧位不留副本；
- **幂等**：新位已有内容则**不搬**（不合并不覆盖），记诊断日志提示人工处理；
- **分治边界**：后端只管自己的域（extensions/skills/prompts/personas）——
  agents 两半共享不动，前端域（ui-settings/ui-state/keybindings/themes）
  归 nova-tui 各自的迁移。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import List, Union

from nova_harness.core.config.defaults import get_project_base_dir
from nova_harness.core.types.package import BACKEND_HALF_DIR_NAME

logger = logging.getLogger(__name__)

# 参与迁移的散养资源目录名（与 TOP_LEVEL_RESOURCE_TYPE_DIRS 的 backend 半区条目一致；
# agents 两半共享不动，tools/user_tools 无顶层自动发现）。
MIGRATED_RESOURCE_DIR_NAMES = ("extensions", "skills", "prompts", "personas")


def migrate_backend_resource_dirs(base_dir: Union[str, Path]) -> List[str]:
    """把 *base_dir* 下的旧位散养资源目录整体搬入 ``backend/`` 半区。

    返回迁移/诊断消息列表（供调用方记日志）；无旧位时为空列表（零副作用）。
    """
    base = Path(base_dir)
    backend_dir = base / BACKEND_HALF_DIR_NAME
    messages: List[str] = []

    for name in MIGRATED_RESOURCE_DIR_NAMES:
        old_dir = base / name
        if not old_dir.is_dir():
            continue
        new_dir = backend_dir / name
        if new_dir.exists():
            message = (
                f"目录迁移跳过（新位已有内容，不合并不覆盖）：{old_dir} → {new_dir}"
                f"——请人工合并后删除旧位"
            )
            logger.warning("%s", message)
            messages.append(message)
            continue
        backend_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_dir), str(new_dir))
        message = f"目录迁移完成：{old_dir} → {new_dir}"
        logger.info("%s", message)
        messages.append(message)

    return messages


def migrate_backend_layout(
    *,
    cwd: Union[str, Path],
    agent_dir: Union[str, Path],
) -> List[str]:
    """迁移 user 与 project 两级 base 的后端散养资源目录（启动单一入口）。

    两级各管各的 base；同一物理目录（极端自定义 agent_dir 与项目 .nova 重合）
    只迁一次。迁移消息经本模块 logger 输出并原样返回（测试断言用）。
    """
    bases: List[Path] = []
    for candidate in (Path(agent_dir), get_project_base_dir(cwd)):
        resolved = candidate.resolve()
        if resolved not in bases:
            bases.append(resolved)

    messages: List[str] = []
    for base in bases:
        messages.extend(migrate_backend_resource_dirs(base))
    return messages


__all__ = [
    "MIGRATED_RESOURCE_DIR_NAMES",
    "migrate_backend_layout",
    "migrate_backend_resource_dirs",
]
