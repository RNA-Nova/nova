"""granular 五开关审批金标测试（A2a——对位 codex 锚点 39f4277087）。

移植源：
- 访问器测试逐例对位 codex `protocol/src/protocol.rs:4978-5067`（四个
  `granular_approval_config_*` 用例）；
- serde 外部标签形状钉（unit 变体 → kebab-case 字符串；granular →
  `{"granular": {...}}` 外壳、负载内字段 snake_case）对位
  `protocol.rs:1026-1050` 的 serde 标注与
  `app-server-protocol/src/protocol/v2/tests.rs:2030-2070` 的
  `ask_for_approval_granular_*` 用例；
- `untrusted` 拒收对位 `core/src/config/mod.rs:229`（
  UnsupportedUntrustedApprovalPolicyError 文案）与
  `core/src/config/config_tests.rs:11584-11606`（
  explicit_untrusted_approval_policy_is_rejected）。

注：本文件从 `nova_protocol.exec_server_config` 子模块直引——
`GranularApprovalConfig` / `ApprovalPolicySetting` 的门面（`nova_protocol
__init__`）导出不在 A2a 许可改动清单内，留待主任务补挂。
"""

from __future__ import annotations

from itertools import product

import pytest
from nova_protocol.exec_server_config import (
    ApprovalPolicy,
    ExecutorConfig,
    GranularApprovalConfig,
    ResolvedExecutionPolicy,
)
from pydantic import ValidationError

#: 全 True 负载（五布尔全组合测试的基准形状）
_FULL_GRANULAR_PAYLOAD = {
    "sandbox_approval": True,
    "rules": True,
    "skill_approval": True,
    "request_permissions": True,
    "mcp_elicitations": True,
}


def _granular(**overrides: bool) -> GranularApprovalConfig:
    """构造五开关负载（缺省全 False，按 overrides 翻转）"""
    base = {key: False for key in _FULL_GRANULAR_PAYLOAD}
    base.update(overrides)
    return GranularApprovalConfig(**base)


class TestGranularApprovalConfigAccessors:
    """访问器语义逐例移植（对位 codex protocol.rs:4978-5067）"""

    def test_mcp_elicitation_flag_is_field_driven(self):
        # 对位 granular_approval_config_mcp_elicitation_flag_is_field_driven
        assert _granular(mcp_elicitations=True).allows_mcp_elicitations()
        assert not _granular(mcp_elicitations=False).allows_mcp_elicitations()

    def test_skill_approval_flag_is_field_driven(self):
        # 对位 granular_approval_config_skill_approval_flag_is_field_driven
        assert _granular(skill_approval=True).allows_skill_approval()
        assert not _granular(skill_approval=False).allows_skill_approval()

    def test_request_permissions_flag_is_field_driven(self):
        # 对位 granular_approval_config_request_permissions_flag_is_field_driven
        assert _granular(request_permissions=True).allows_request_permissions()
        assert not _granular(request_permissions=False).allows_request_permissions()

    def test_sandbox_approval_and_rules_flags_are_field_driven(self):
        # nova 补齐（codex 未单列）：同语义钉住另外两个访问器
        assert _granular(sandbox_approval=True).allows_sandbox_approval()
        assert not _granular(sandbox_approval=False).allows_sandbox_approval()
        assert _granular(rules=True).allows_rules_approval()
        assert not _granular(rules=False).allows_rules_approval()

    def test_defaults_missing_optional_flags_to_false(self):
        # 对位 granular_approval_config_defaults_missing_optional_flags_to_false
        # （serde default=false 的两个可选字段缺省落 False）
        decoded = GranularApprovalConfig.model_validate(
            {
                "sandbox_approval": True,
                "rules": False,
                "mcp_elicitations": True,
            }
        )
        assert decoded == GranularApprovalConfig(
            sandbox_approval=True,
            rules=False,
            skill_approval=False,
            request_permissions=False,
            mcp_elicitations=True,
        )


class TestSerdeExternalTagShape:
    """serde 外部标签形状钉（对位 protocol.rs:1026-1050 serde 标注 +
    v2 tests.rs:2030-2070；经 ExecutorConfig.approval_policy 联合字段验证）"""

    def test_never_roundtrip(self):
        cfg = ExecutorConfig.model_validate({"approval_policy": "never"})
        assert cfg.approval_policy is ApprovalPolicy.NEVER
        assert cfg.model_dump()["approval_policy"] == "never"

    def test_on_request_roundtrip(self):
        cfg = ExecutorConfig.model_validate({"approval_policy": "on-request"})
        assert cfg.approval_policy is ApprovalPolicy.ON_REQUEST
        assert cfg.model_dump()["approval_policy"] == "on-request"

    def test_on_failure_alias_parses_to_on_request(self):
        # 对位 serde #[serde(alias = "on-failure")]：alias 只影响反序列化
        # 方向——"on-failure" 入、ON_REQUEST 归，dump 回 "on-request"
        cfg = ExecutorConfig.model_validate({"approval_policy": "on-failure"})
        assert cfg.approval_policy is ApprovalPolicy.ON_REQUEST
        assert cfg.model_dump()["approval_policy"] == "on-request"

    def test_granular_roundtrip_byte_shape(self):
        # 逐字节对位 serde 外部标签：{"granular": {...}} 外壳 +
        # 负载内 snake_case 字段名（codex 侧无 rename 属性）
        wire = {"approval_policy": {"granular": dict(_FULL_GRANULAR_PAYLOAD)}}
        cfg = ExecutorConfig.model_validate(wire)
        assert cfg.approval_policy == GranularApprovalConfig(**_FULL_GRANULAR_PAYLOAD)
        assert cfg.model_dump()["approval_policy"] == {
            "granular": _FULL_GRANULAR_PAYLOAD
        }

    def test_granular_roundtrip_all_flag_combinations(self):
        # 五布尔全组合（2^5=32）：roundtrip 逐字节不动
        for flags in product([False, True], repeat=5):
            payload = dict(zip(_FULL_GRANULAR_PAYLOAD, flags))
            cfg = ExecutorConfig.model_validate(
                {"approval_policy": {"granular": payload}}
            )
            assert cfg.approval_policy == GranularApprovalConfig(**payload)
            assert cfg.model_dump()["approval_policy"] == {"granular": payload}

    def test_granular_defaults_via_wrapped_shell(self):
        # 对位 v2 ask_for_approval_granular_defaults_missing_optional_flags_to_false
        # （经外部标签外壳路径）
        cfg = ExecutorConfig.model_validate(
            {
                "approval_policy": {
                    "granular": {
                        "sandbox_approval": True,
                        "rules": False,
                        "mcp_elicitations": True,
                    }
                }
            }
        )
        assert cfg.approval_policy == GranularApprovalConfig(
            sandbox_approval=True,
            rules=False,
            skill_approval=False,
            request_permissions=False,
            mcp_elicitations=True,
        )

    def test_bare_granular_dict_accepted(self):
        # nova 便利超集（serde 不收裸负载）：无外壳 dict 直接落负载模型
        cfg = ExecutorConfig.model_validate(
            {"approval_policy": dict(_FULL_GRANULAR_PAYLOAD)}
        )
        assert cfg.approval_policy == GranularApprovalConfig(**_FULL_GRANULAR_PAYLOAD)

    def test_unknown_variant_rejected(self):
        with pytest.raises(ValidationError):
            ExecutorConfig.model_validate({"approval_policy": "sometimes"})
        with pytest.raises(ValidationError):
            ExecutorConfig.model_validate(
                {"approval_policy": {"sometimes": dict(_FULL_GRANULAR_PAYLOAD)}}
            )

    def test_granular_missing_required_fields_rejected(self):
        # sandbox_approval / rules / mcp_elicitations 三个必填（对位 codex
        # 无 serde default 的三字段）
        with pytest.raises(ValidationError):
            ExecutorConfig.model_validate(
                {"approval_policy": {"granular": {"sandbox_approval": True}}}
            )


class TestUntrustedRejected:
    """untrusted 拒收（对位 codex config mod.rs:229 文案语义 +
    config_tests.rs:11584 explicit_untrusted_approval_policy_is_rejected）"""

    def test_untrusted_string_rejected_with_codex_message(self):
        with pytest.raises(
            ValidationError,
            match='approval_policy = "untrusted" is no longer supported',
        ):
            ExecutorConfig.model_validate({"approval_policy": "untrusted"})

    def test_untrusted_full_message(self):
        with pytest.raises(ValidationError) as exc_info:
            ExecutorConfig(approval_policy="untrusted")
        # 文案逐字对位 UnsupportedUntrustedApprovalPolicyError
        assert (
            'approval_policy = "untrusted" is no longer supported; '
            "remove this setting"
        ) in str(exc_info.value)


class TestConsumerCompat:
    """消费面兼容钉（bundles/harness 以 `is`/`in` 枚举比较消费——字符串档
    语义不变，granular 实例安全落非 NEVER 分支）"""

    def test_default_is_on_request(self):
        assert ExecutorConfig().approval_policy is ApprovalPolicy.ON_REQUEST

    def test_enum_members_unchanged(self):
        # 枚举三档不动（test_network_gate.py 迭代消费）
        assert list(ApprovalPolicy) == [
            ApprovalPolicy.ON_REQUEST,
            ApprovalPolicy.ON_FAILURE,
            ApprovalPolicy.NEVER,
        ]

    def test_enum_identity_comparisons_hold(self):
        # 对位消费点写法：bundles adjudication `is ApprovalPolicy.NEVER` /
        # harness orchestrator `in (ApprovalPolicy.ON_REQUEST,)`
        assert ExecutorConfig(approval_policy="never").approval_policy is (
            ApprovalPolicy.NEVER
        )
        assert ExecutorConfig().approval_policy in (ApprovalPolicy.ON_REQUEST,)

    def test_direct_enum_construction_passthrough(self):
        # 存量 Python 直构路径（含 ON_FAILURE）语义不变
        cfg = ExecutorConfig(approval_policy=ApprovalPolicy.ON_FAILURE)
        assert cfg.approval_policy is ApprovalPolicy.ON_FAILURE

    def test_granular_instance_is_not_never(self):
        # granular 实例在 `is NEVER` 判比较下安全落 False（不冒充字符串档）
        cfg = ExecutorConfig(
            approval_policy=GranularApprovalConfig(**_FULL_GRANULAR_PAYLOAD)
        )
        assert cfg.approval_policy is not ApprovalPolicy.NEVER
        assert cfg.approval_policy not in (ApprovalPolicy.ON_REQUEST,)

    def test_resolve_execution_carries_granular(self):
        # 物化值对象透传 granular（ResolvedExecutionPolicy 联合注解）
        cfg = ExecutorConfig(approval_policy={"granular": dict(_FULL_GRANULAR_PAYLOAD)})
        resolved = cfg.resolve_execution(None)
        assert isinstance(resolved, ResolvedExecutionPolicy)
        assert resolved.approval_policy == GranularApprovalConfig(
            **_FULL_GRANULAR_PAYLOAD
        )
