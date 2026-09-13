"""后台任务卫生审计——裸 create_task/ensure_future 的机械禁则。

规则：**事件循环原语（create_task/ensure_future）的返回值必须被持有**——
赋值、登记、返回皆可；唯独不允许"裸表达式语句"（创建即丢弃引用）。
事件循环对在途任务只持弱引用，无持有者的任务可能被 GC 中途回收
（流式静默冻死 / 任务蒸发无报错）。

合法的 fire-and-forget 通道（引用由通道内部持有）：
- ``EventStream.drive()``——流自持驱动任务（1:1）；
- ``TaskTracker.spawn()``——属主台账登记（1:N）。

词法作用域竞速（函数内 create + await/cancel）因结果被赋值/await 而
天然合规。唯一的合法例外是**语义上无主**的任务（如 race_with_abort
中被放弃操作的后台观察通道——调用方已放弃它，不存在天然属主），
以行内标记 ``# guard: detached`` 加注释豁免。批次 ③ 把扫描面扩到
nova_harness / nova_server。
"""

import ast
from pathlib import Path

PACKAGES_ROOT = Path(__file__).resolve().parents[3]
SCAN_ROOTS = [
    PACKAGES_ROOT / "nova_protocol" / "src",
    PACKAGES_ROOT / "nova_ai" / "src",
    PACKAGES_ROOT / "nova_agent" / "src",
]

_SPAWN_METHODS = {"create_task", "ensure_future"}


def _is_loop_spawn_call(func: ast.expr) -> bool:
    """是否为事件循环的 spawn 调用（asyncio.create_task / asyncio.ensure_future /
    loop.create_task / asyncio.get_running_loop().create_task）。

    只认"循环直出"形态——``tg.create_task``（TaskGroup 自持引用）等
    属主/容器方法的同名调用不在禁则内。
    """
    if not isinstance(func, ast.Attribute) or func.attr not in _SPAWN_METHODS:
        return False
    receiver = func.value
    # asyncio.create_task(...) / asyncio.ensure_future(...)
    if isinstance(receiver, ast.Name) and receiver.id in ("asyncio", "loop"):
        return True
    # asyncio.get_running_loop().create_task(...) / loop.get_... 变体
    if isinstance(receiver, ast.Call) and isinstance(receiver.func, ast.Attribute):
        return receiver.func.attr in ("get_running_loop", "get_event_loop")
    return False


def _bare_spawn_lines(path: Path):
    """AST 层面的裸 spawn 语句行号（调用目标必须是循环原语形态），
    扣除行内 ``# guard: detached`` 豁免。"""
    text = path.read_text(encoding="utf-8")
    exempted = {
        lineno
        for lineno, line in enumerate(text.splitlines(), start=1)
        if "# guard: detached" in line
    }
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            if _is_loop_spawn_call(node.value.func) and node.lineno not in exempted:
                yield node.lineno


def test_no_bare_background_tasks():
    """src 内不得出现裸 create_task/ensure_future 语句（返回值无人持有）。"""
    offenders = []
    for root in SCAN_ROOTS:
        for path in sorted(root.rglob("*.py")):
            for lineno in _bare_spawn_lines(path):
                offenders.append(f"{path.relative_to(PACKAGES_ROOT)}:{lineno}")
    assert not offenders, (
        "裸 create_task/ensure_future（返回值无人持有，任务可能被 GC 蒸发）；"
        "fire-and-forget 请走 EventStream.drive() / TaskTracker.spawn()：\n"
        + "\n".join(offenders)
    )
