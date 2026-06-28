"""
扩展类型单元测试：ExtensionEventBus 等。
"""

from nova_harness.core.types.extensions import (
    Extension,
    ExtensionCommand,
    ExtensionEventBus,
    ExtensionFlag,
    ExtensionMessageRenderer,
    ExtensionProviderRegistration,
    ExtensionShortcut,
    ExtensionToolDefinition,
    LoadedExtensionsResult,
)


def test_loaded_extensions_result_defaults():
    result = LoadedExtensionsResult()
    assert result.extensions == []
    assert result.diagnostics == []


def test_extension_command_defaults():
    cmd = ExtensionCommand(name="c")
    assert cmd.description is None
    assert cmd.extension_path is None
    assert cmd.handler() is None


def test_extension_shortcut_defaults():
    sc = ExtensionShortcut(key="k")
    assert sc.description is None
    assert sc.extension_path is None
    assert sc.handler() is None


def test_extension_flag_defaults():
    flag = ExtensionFlag(name="f")
    assert flag.type == "boolean"
    assert flag.default is None


def test_extension_message_renderer_defaults():
    renderer = ExtensionMessageRenderer(custom_type="note")
    assert renderer.renderer("x") is None
    assert renderer.extension_path is None


def test_extension_provider_registration():
    reg = ExtensionProviderRegistration(name="p", config={"x": 1})
    assert reg.name == "p"
    assert reg.config == {"x": 1}


def test_extension_defaults():
    ext = Extension(path="/x", name="x")
    assert ext.handlers == {}
    assert ext.tools == []
    assert ext.commands == []


def test_event_bus_subscribe_and_emit():
    bus = ExtensionEventBus()
    calls = []

    def handler(x):
        calls.append(x)
        return x * 2

    bus.on("evt", handler)
    results = bus.emit("evt", 5)
    assert calls == [5]
    assert results == [10]


def test_event_bus_error_in_handler_captured():
    bus = ExtensionEventBus()

    def good(x):
        return x

    def bad(x):
        raise ValueError("oops")

    bus.on("evt", good)
    bus.on("evt", bad)
    results = bus.emit("evt", 1)
    assert results[0] == 1
    assert isinstance(results[1], ValueError)


def test_event_bus_remove_handler():
    bus = ExtensionEventBus()
    calls = []

    def handler(x):
        calls.append(x)

    remove = bus.on("evt", handler)
    remove()
    bus.emit("evt", 1)
    assert calls == []


def test_event_bus_remove_unknown_handler_safe():
    bus = ExtensionEventBus()
    remove = bus.on("evt", lambda x: x)
    remove()
    remove()  # 重复移除不应报错


def test_event_bus_emit_no_handlers():
    bus = ExtensionEventBus()
    assert bus.emit("none") == []


def test_event_bus_clear():
    bus = ExtensionEventBus()
    bus.on("a", lambda: None)
    bus.clear()
    assert bus._handlers == {}


def test_extension_tool_definition_is_tool_definition():
    tool = ExtensionToolDefinition(name="t", description="desc")
    assert tool.name == "t"
