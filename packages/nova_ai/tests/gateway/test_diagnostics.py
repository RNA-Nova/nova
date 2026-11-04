"""DiagnosticsCollector（gateway/diagnostics.py）测试——双通道装置。"""

from nova_protocol import NovaDiagnostic, WarningKind

from nova_ai.gateway import DiagnosticsCollector


def _d(message: str = "x") -> NovaDiagnostic:
    return NovaDiagnostic(
        severity="warning", kind=WarningKind.CAPABILITY, message=message
    )


class TestDiagnosticsCollector:
    """无条件收集 + 可选实时槽。"""

    def test_collect_without_listener(self):
        """槽位空缺：纯批模式，零丢失。"""
        collector = DiagnosticsCollector()
        collector.append(_d("a"))
        collector.append(_d("b"))
        assert [d.message for d in collector.items] == ["a", "b"]

    def test_live_slot_fires_and_still_collects(self):
        """槽位在场：append 同步外发，且收集不丢。"""
        fired = []
        collector = DiagnosticsCollector(on_diagnostic=fired.append)
        d = _d()
        collector.append(d)
        assert fired == [d]
        assert collector.items == [d]

    def test_slot_exception_never_breaks_append(self):
        """槽回调炸：只记日志，收集照常（fail-safe 纪律）。"""

        def bad(_d):
            raise RuntimeError("boom")

        collector = DiagnosticsCollector(on_diagnostic=bad)
        collector.append(_d())  # 不抛
        assert len(collector.items) == 1

    def test_read_protocol(self):
        """只读容器协议（len/索引/迭代）——读面与 list 一致。"""
        collector = DiagnosticsCollector()
        collector.append(_d("a"))
        collector.append(_d("b"))
        assert len(collector) == 2
        assert collector[0].message == "a"
        assert [d.message for d in collector] == ["a", "b"]
