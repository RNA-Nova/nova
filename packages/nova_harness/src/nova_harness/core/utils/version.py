"""版本戳（单一事实源：pyproject.toml 的 ``version`` 字段）。

- pip 安装形态：importlib.metadata 读已安装 dist 的元数据；
- 冻结形态（PyInstaller）：构建期 ``--collect-all`` 把 dist-info 打进
  二进制，importlib.metadata 经冻结钩子同样命中；
- 兜底（元数据缺失的非常规运行）：返回 ``0.0.0+unknown``——调用方只用于
  展示，不参与逻辑裁决。
"""

from importlib import metadata

# PyPI dist 名（pyproject.toml 的 [tool.poetry] name）
_DIST_NAME = "nova-harness"

_UNKNOWN = "0.0.0+unknown"


def harness_version() -> str:
    """当前 nova-harness 版本；元数据缺失返回占位串（不抛）。"""
    try:
        return metadata.version(_DIST_NAME)
    except metadata.PackageNotFoundError:
        return _UNKNOWN
