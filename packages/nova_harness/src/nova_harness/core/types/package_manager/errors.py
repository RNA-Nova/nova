"""包管理器异常类型。"""


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


__all__ = ["AmbiguousPackageNameError"]
