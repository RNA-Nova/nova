"""03 - 最小扩展：session_start 钩子 + 自定义 slash 命令

扩展是一个暴露 ``extension(nova)`` 工厂函数的 ``.py`` 文件，放进 user 级
扩展目录 ``<agent_dir>/backend/extensions/`` 即被会话自动发现与装载
（其他来源：已安装包的 ``[tool.nova] extensions`` 声明、项目级
``<cwd>/.nova/backend/extensions/``、settings 显式路径）。

本示例演示扩展的完整生命周期：
1. 把扩展文件写进临时 agent_dir 的扩展目录；
2. ``create_agent_session`` —— 扩展被发现、加载，``ExtensionRunner`` 就绪；
3. ``session.bind_extensions()`` —— 发出 ``session_start``，扩展钩子执行
   （``create_agent_session_runtime()`` 会自动完成这一步；直接用
   ``create_agent_session()`` 时需要像这里一样自行调用一次）；
4. 经 ``session.prompt("/demo-hello ...")`` 执行扩展注册的 slash 命令；
5. ``session.dispose()`` 释放会话。

全程无需 API Key：扩展命令在 ``prompt()`` 的模型校验之前被消费，
不触发任何模型调用。

运行：
    python examples/03_extension.py
"""

import asyncio
import tempfile
from pathlib import Path

from nova_harness import CreateAgentSessionOptions, create_agent_session
from nova_harness.core.harness.session import SessionManager

# ----------------------------------------------------------------------
# 扩展源码：本示例把它写入临时目录，真实开发中这就是一个独立的 .py 文件
# ----------------------------------------------------------------------
EXTENSION_SOURCE = '''"""demo_hello —— 最小扩展示例：session_start 钩子 + /demo-hello 命令。"""


def extension(nova):
    """扩展工厂：装载期收到注册面 nova（NovaExtensionAPI），只做声明式注册。

    注册（nova.on / register_command / register_flag / ...）不依赖会话，
    在装载期申报"我有什么"；运行期动作（读写会话、弹窗、执行命令等）
    统一经事件 handler 收到的 ctx（ExtensionContext）触达。
    """

    def on_session_start(event, ctx):
        # 事件 handler 签名：(event, ctx)，同步或 async 均可。
        # ctx 上有 has_ui / cwd / session_manager / model_runtime 等。
        print(f"    [扩展] session_start: reason={event.reason!r}, has_ui={ctx.has_ui}")

    async def on_demo_hello(args, ctx):
        # 命令 handler 签名：(args, ctx)，框架会 await 它，因此需为 async；
        # args 是 "/demo-hello ..." 空格之后的全部文本。
        # 真实扩展通常经 ctx.ui / ctx.send_message 与用户交互，
        # 这里用 print 直出以便观察。
        name = args.strip() or "世界"
        ctx.set_session_name(f"demo-hello: {name}")
        entry_id = ctx.append_entry("demo_hello", {"greeting": name})
        print(f"    [扩展] /demo-hello 已执行：会话命名 + 自定义条目 {entry_id}")

    nova.on("session_start", on_session_start)
    nova.register_command(
        "demo-hello",
        {
            "description": "示例命令：命名会话并追加一条自定义会话条目",
            "handler": on_demo_hello,
        },
    )
'''


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        cwd = workdir / "ws"
        agent_dir = workdir / "agent"
        cwd.mkdir(parents=True)

        # 1. 把扩展文件放进 user 级扩展目录（agent_dir 即 user 配置根，
        #    这里指向临时目录，不碰 ~/.nova/agent）
        extensions_dir = agent_dir / "backend" / "extensions"
        extensions_dir.mkdir(parents=True)
        extension_path = extensions_dir / "demo_hello.py"
        extension_path.write_text(EXTENSION_SOURCE, encoding="utf-8")
        print(f"1. 扩展已写入 {extension_path}")

        # 2. 创建会话：扩展随资源加载被发现；内存态会话不落盘
        result = await create_agent_session(
            CreateAgentSessionOptions(
                cwd=cwd,
                agent_dir=agent_dir,
                session_manager=SessionManager.in_memory(str(cwd)),
            )
        )
        session = result.session
        loaded = result.extensions_result
        print(
            f"2. 会话已创建：加载扩展 {len(loaded.extensions)} 个"
            f"（{extension_path.name}），加载错误 {len(loaded.errors)} 个"
        )

        # 3. 显式触发 session_start —— 扩展的钩子在这里执行
        print("3. 调用 session.bind_extensions()，触发 session_start：")
        await session.bind_extensions()

        # 4. 列出当前可用的 slash 命令（扩展命令即在此注册）
        runner = session.extension_runner
        commands = runner.get_registered_commands() if runner else []
        for cmd in commands:
            print(f"4. 已注册命令: /{cmd.resolved_name} —— {cmd.description}")

        # 5. 经 prompt 执行扩展命令（被 prompt 直接消费，不进入模型流程）
        print("5. 执行 /demo-hello Nova：")
        await session.prompt("/demo-hello Nova")

        # 6. 验证命令的副作用：会话命名 + 自定义条目已进入会话账本
        entries = session.session_manager.get_entries()
        custom = [e for e in entries if getattr(e, "custom_type", "") == "demo_hello"]
        print(
            f"6. 验证：会话名 {session.session_manager.get_session_name()!r}，"
            f"demo_hello 条目 {len(custom)} 条（data={custom[0].data if custom else None}）"
        )

        # 7. 释放会话资源（扩展 runner 一并失效）
        session.dispose()
        print("7. 会话已 dispose")


if __name__ == "__main__":
    asyncio.run(main())
