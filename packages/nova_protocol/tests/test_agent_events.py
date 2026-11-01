"""WarningEvent（agent_events.py）测试——默认值/roundtrip/线形态。"""

from nova_protocol import WarningEvent, WarningKind


class TestWarningEvent:
    def test_defaults(self):
        event = WarningEvent(message="x")
        assert event.type == "warning"
        assert event.kind is WarningKind.GENERAL

    def test_roundtrip(self):
        event = WarningEvent(
            kind=WarningKind.CAPABILITY, message="fd 缺失，brew install fd"
        )
        restored = WarningEvent.model_validate(event.model_dump())
        assert restored == event
        # 内存中保持枚举成员（不依赖 use_enum_values）
        assert restored.kind is WarningKind.CAPABILITY

    def test_dump_wire(self):
        event = WarningEvent(kind=WarningKind.CONFIG, message="配置已迁移")
        assert event.dump_wire() == {
            "type": "warning",
            "kind": "config",
            "message": "配置已迁移",
        }
