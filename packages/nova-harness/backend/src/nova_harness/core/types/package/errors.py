"""包管理器异常类型。"""

from typing import List, Tuple


class AmbiguousPackageNameError(ValueError):
    """同一名称在多个源或 scope 中匹配到多个包时抛出。

    调用方应提示用户使用 source spec 而不是 name 来指定具体包。
    """

    def __init__(self, name: str, message: str = "") -> None:
        self.name = name
        if not message:
            message = (
                f"Multiple packages named '{name}' are installed. "
                "Use the source spec instead of the name."
            )
        super().__init__(message)


class PackageInstallError(RuntimeError):
    """批量自动安装时部分包失败。"""

    def __init__(self, failures: List[Tuple[str, BaseException]]) -> None:
        self.failures = failures
        messages = "\n".join(f"  - {source}: {exc}" for source, exc in failures)
        super().__init__(f"Failed to install {len(failures)} package(s):\n{messages}")


class PackageUpdateError(RuntimeError):
    """跨 scope 更新时部分 scope 失败。"""

    def __init__(self, successful, failures):
        self.successful = successful
        self.failures = failures
        super().__init__(
            "Update succeeded in {} but failed in {}.".format(
                ", ".join(scope for scope, _ in successful),
                ", ".join(scope for scope, _ in failures),
            )
        )


__all__ = [
    "AmbiguousPackageNameError",
    "PackageInstallError",
    "PackageUpdateError",
]
