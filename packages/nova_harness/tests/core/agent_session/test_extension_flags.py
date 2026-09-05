"""扩展 flag 装配期应用的单元测试（services._apply_extension_flag_values）。

CLI 透传的值只有 True / 字符串两种形态（--name / --name=value），
类型归一化（number coercion）与未注册名诊断都在这里发生。
"""

from types import SimpleNamespace

from nova_harness.core.agent_session.services import AgentSessionServices
from nova_harness.core.types.extensions.commands import ExtensionFlag


def _extensions_result(*flags: ExtensionFlag):
    """构造最小 LoadedExtensionsResult 替身（flag 注册表 + 内存值表）。"""
    extension = SimpleNamespace(flags={f.name: f for f in flags})
    runtime = SimpleNamespace(flag_values={})
    return SimpleNamespace(extensions=[extension], runtime=runtime)


def test_boolean_flag_any_value_becomes_true():
    """布尔 flag 是指名开关：只要出现即 true（含 --flag=false 形）。"""
    result = _extensions_result(ExtensionFlag(name="plan", type="boolean"))

    diagnostics = AgentSessionServices._apply_extension_flag_values(
        result, {"plan": True}
    )

    assert diagnostics == []
    assert result.runtime.flag_values == {"plan": True}

    AgentSessionServices._apply_extension_flag_values(result, {"plan": "false"})
    assert result.runtime.flag_values["plan"] is True


def test_string_flag_keeps_value_and_rejects_bare():
    result = _extensions_result(ExtensionFlag(name="tag", type="string"))

    diagnostics = AgentSessionServices._apply_extension_flag_values(
        result, {"tag": "nightly"}
    )
    assert diagnostics == []
    assert result.runtime.flag_values == {"tag": "nightly"}

    diagnostics = AgentSessionServices._apply_extension_flag_values(
        result, {"tag": True}
    )
    assert len(diagnostics) == 1
    assert 'Extension flag "--tag" requires a string value' in diagnostics[0].message


def test_number_flag_coerces_cli_string():
    """CLI 形全是字符串——数字 flag 在此归一化（"5"→5、"0.5"→0.5）。"""
    result = _extensions_result(ExtensionFlag(name="depth", type="number"))

    diagnostics = AgentSessionServices._apply_extension_flag_values(
        result, {"depth": "5"}
    )
    assert diagnostics == []
    assert result.runtime.flag_values == {"depth": 5}

    diagnostics = AgentSessionServices._apply_extension_flag_values(
        result, {"depth": "0.5"}
    )
    assert diagnostics == []
    assert result.runtime.flag_values == {"depth": 0.5}

    diagnostics = AgentSessionServices._apply_extension_flag_values(
        result, {"depth": "abc"}
    )
    assert len(diagnostics) == 1
    assert 'Extension flag "--depth" requires a numeric value' in diagnostics[0].message


def test_unknown_flag_produces_diagnostic():
    result = _extensions_result(ExtensionFlag(name="plan", type="boolean"))

    diagnostics = AgentSessionServices._apply_extension_flag_values(
        result, {"paln": True, "other": "x"}
    )

    assert len(diagnostics) == 1
    assert diagnostics[0].type == "error"
    assert "Unknown options: --paln, --other" in diagnostics[0].message
    # 未知名不写入值表
    assert result.runtime.flag_values == {}
