"""安装世界（写）：包物化、元数据持久化、更新与 Python 环境后端。

- ``installer``: 单 scope 的安装 / 卸载 / 更新 / 列表入口 ``PackageInstaller``；
- ``store``: dist-info 安装事实快照读写与安装路径计算；
- ``updates``: git 源更新可用性检查；
- ``python_backend``: uv / pip 安装后端。
"""

from nova_harness.core.package.install.installer import PackageInstaller

__all__ = ["PackageInstaller"]
