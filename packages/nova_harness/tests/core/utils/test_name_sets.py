"""name_sets 名单代数测试（名字级）。

语义用例与路径级 apply_patterns（discovery.py）逐条对照——同一语法、
两个作用域，防漂移。
"""

from nova_harness.core.utils.name_sets import (
    apply_name_list,
    build_selection_report,
    resolve_name_set,
    split_patterns,
    unmatched_entries,
)

ALL = ["bash", "read", "write", "edit", "grep", "find"]


class TestThreeStates:
    def test_none_is_unrestricted(self):
        assert apply_name_list(ALL, None) == set(ALL)

    def test_empty_list_disables_all(self):
        assert apply_name_list(ALL, []) == set()

    def test_include_list(self):
        assert apply_name_list(ALL, ["bash", "read"]) == {"bash", "read"}


class TestPrefixVocabulary:
    def test_exclude_only_means_all_minus(self):
        assert apply_name_list(ALL, ["!bash"]) == set(ALL) - {"bash"}

    def test_include_then_exclude(self):
        result = apply_name_list(ALL, ["bash", "read", "!read"])
        assert result == {"bash"}

    def test_force_include_overrides_exclude(self):
        result = apply_name_list(ALL, ["!read", "+read"])
        assert "read" in result

    def test_force_exclude_overrides_force_include(self):
        result = apply_name_list(ALL, ["!read", "+read", "-read"])
        assert "read" not in result

    def test_force_include_cannot_exceed_universe(self):
        """+ 只能从候选全集找回，不能凭空造名。"""
        result = apply_name_list(ALL, ["+ghost"])
        assert result == set(ALL)

    def test_whitespace_and_empty_entries_ignored(self):
        assert apply_name_list(ALL, [" bash ", "", "!"]) == {"bash"}


class TestLayeredResolution:
    def test_layers_intersect(self):
        result = resolve_name_set(ALL, ["bash", "read", "grep"], ["!grep"])
        assert result == {"bash", "read"}

    def test_no_layer_can_resurrect(self):
        """任何一层都不能复活上游裁掉的名字（收窄不可逆）。"""
        result = resolve_name_set(ALL, ["!bash"], ["+bash", "read"])
        assert "bash" not in result
        assert "read" in result

    def test_order_irrelevant(self):
        a = resolve_name_set(ALL, ["bash", "read"], ["!read"])
        b = resolve_name_set(ALL, ["!read"], ["bash", "read"])
        assert a == b == {"bash"}

    def test_none_layers_skip(self):
        assert resolve_name_set(ALL, None, ["bash"], None) == {"bash"}

    def test_empty_layer_kills_all(self):
        assert resolve_name_set(ALL, ["bash"], []) == set()


class TestSplitPatterns:
    def test_groups(self):
        inc, exc, fi, fe = split_patterns(["a", "!b", "+c", "-d"])
        assert inc == {"a"}
        assert exc == {"b"}
        assert fi == {"c"}
        assert fe == {"d"}


class TestUnmatchedEntries:
    def test_reports_all_kinds(self):
        missing = unmatched_entries(ALL, ["bash", "ghost", "!nope", "+nil", "-nix"])
        assert set(missing) == {"ghost", "!nope", "+nil", "-nix"}

    def test_hit_is_not_reported(self):
        assert unmatched_entries(ALL, ["bash", "!read"]) == []

    def test_none_and_empty(self):
        assert unmatched_entries(ALL, None) == []
        assert unmatched_entries(ALL, []) == []


class TestBuildSelectionReport:
    """yaml 点名项裁决报告：全状态 × 含/不含 settings/sdk 层信息。"""

    def test_none_name_list_returns_empty(self):
        assert build_selection_report("tools", None, ALL) == []

    def test_empty_name_list_has_no_includes(self):
        assert build_selection_report("tools", [], ALL) == []

    def test_all_statuses_with_full_layer_info(self):
        report = build_selection_report(
            "tools",
            ["read", "bash", "grep", "ghost"],
            ["read", "bash", "grep"],
            surviving_after_settings={"read", "grep"},  # bash 被 settings 裁
            surviving_after_sdk={"read"},  # grep 被 sdk 裁
            final={"read"},
        )
        assert {(s.name, s.status) for s in report} == {
            ("read", "ok"),
            ("bash", "disabled_by_settings"),
            ("grep", "disabled_by_sdk"),
            ("ghost", "missing"),
        }
        assert all(s.resource_type == "tools" for s in report)

    def test_missing_checked_before_settings(self):
        """不在候选集的名字优先报 missing（即使 settings 层信息同样不含它）。"""
        report = build_selection_report(
            "tools", ["ghost"], ["read"], surviving_after_settings={"read"}
        )
        assert [(s.name, s.status) for s in report] == [("ghost", "missing")]

    def test_none_settings_layer_skips_attribution(self):
        """settings 层不可知（None）：穿过该层但不在 final 的项不出报告。"""
        report = build_selection_report(
            "skills",
            ["read", "bash"],
            ["read", "bash"],
            surviving_after_settings=None,
            final={"read"},
        )
        # bash 存在但不在 final，又无可归因层——不出报告（如被 yaml 自身 ! 覆盖）
        assert [(s.name, s.status) for s in report] == [("read", "ok")]

    def test_settings_attribution_without_sdk_layer(self):
        """无 SDK 层（None）：settings 层裁掉的报 disabled_by_settings。"""
        report = build_selection_report(
            "user_tools",
            ["bash", "x"],
            ["bash", "x"],
            surviving_after_settings={"bash"},
            final={"bash"},
        )
        assert {(s.name, s.status) for s in report} == {
            ("bash", "ok"),
            ("x", "disabled_by_settings"),
        }

    def test_none_final_defaults_to_candidates(self):
        report = build_selection_report("skills", ["read", "ghost"], ["read"])
        assert {(s.name, s.status) for s in report} == {
            ("read", "ok"),
            ("ghost", "missing"),
        }

    def test_prefix_entries_not_reported(self):
        """!/+/− 前缀条目是修饰项，不出报告。"""
        report = build_selection_report(
            "tools", ["read", "!ghost", "+nil", "-nix"], ALL
        )
        assert [(s.name, s.status) for s in report] == [("read", "ok")]

    def test_report_sorted_by_name(self):
        report = build_selection_report("tools", ["write", "bash", "read"], ALL)
        assert [s.name for s in report] == ["bash", "read", "write"]
