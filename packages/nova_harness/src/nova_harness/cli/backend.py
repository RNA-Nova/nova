"""nova-server：打包形态的统一入口（``nova-server [rpc|run|pkg] ...``）。

冻结二进制只有一个入口点——argv 首词分发到三个既有 CLI：

- ``rpc``（缺省）：JSON-RPC over stdio（TUI 子进程挂载形态）
- ``run``：print 模式一次性执行（子代理自调走这里）
- ``pkg``：包管理器

开发渠道的 ``nova-harness`` / ``nova-harness-rpc`` / ``nova-pkg`` 三个
启动器不受影响（pip 注册表与这里的分发器挂的是同一批 main 函数）。
"""

import sys

_MODES = ("rpc", "run", "pkg")


def main() -> int:
    argv = sys.argv[1:]
    mode = argv[0] if argv else "rpc"
    rest = argv[1:] if argv else []

    # 顶层 --version：分发器直答（版本戳归 importlib.metadata——冻结态
    # 命中打包元数据；三个子模式各自的 --version 走既有 parser 同值输出）
    if mode == "--version":
        from nova_harness.core.utils.version import harness_version

        print(f"nova-server {harness_version()}")
        return 0

    if mode == "run":
        from nova_harness.cli.main import main as run_main

        result = run_main(["run", *rest])
        return int(result or 0)
    if mode == "pkg":
        from nova_harness.cli.package import main as pkg_main

        result = pkg_main(rest)
        return int(result or 0)
    if mode == "rpc":
        from nova_harness.modes.rpc.cli import main as rpc_main

        sys.argv = ["nova-server", *rest]
        return rpc_main()

    print(
        f"未知模式: {mode}（合法值: {' / '.join(_MODES)}；缺省 rpc）",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
