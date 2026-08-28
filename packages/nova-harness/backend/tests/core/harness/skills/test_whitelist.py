"""filter_skills_by_whitelist 来源分治语义单测。

规则（纯人格裁剪，不承担安全职责）：
- 三态：None=全部放行（含包内）；[]=包内全禁；名单=仅约束包内 skill
  （origin="package"），支持 ``!`` 排除；
- 用户级/项目级/显式路径（其余 origin 与无 source_info）始终放行。
"""

from nova_harness.core.harness.skills import (
    filter_skills_by_whitelist,
    is_package_skill,
)
from nova_harness.core.types.extensions import SourceInfo
from nova_harness.core.types.resources.skills import Skill


def _skill(name: str, origin: str | None) -> Skill:
    source_info = (
        None if origin is None else SourceInfo(path=f"/tmp/{name}", origin=origin)
    )
    return Skill(
        name=name,
        description=name,
        file_path=f"/tmp/{name}/SKILL.md",
        base_dir=f"/tmp/{name}",
        source_info=source_info,
    )


def test_is_package_skill_only_matches_package_origin():
    assert is_package_skill(_skill("a", "package")) is True
    for origin in ("top-level", "local", "auto", None):
        assert is_package_skill(_skill("a", origin)) is False


def test_none_whitelist_allows_all_including_package():
    skills = {
        "bundled-a": _skill("bundled-a", "package"),
        "bundled-b": _skill("bundled-b", "package"),
    }
    # None（未声明）：不设防，包内也全部放行
    assert sorted(filter_skills_by_whitelist(skills, None).keys()) == [
        "bundled-a",
        "bundled-b",
    ]


def test_empty_whitelist_disables_package_skills_only():
    skills = {
        "bundled-a": _skill("bundled-a", "package"),
        "user": _skill("user", "auto"),
    }
    # []（显式空）：包内全禁；用户级 skill 不受 yaml 管辖，仍然放行
    assert sorted(filter_skills_by_whitelist(skills, []).keys()) == ["user"]


def test_non_empty_whitelist_filters_package_skills():
    skills = {
        "bundled-a": _skill("bundled-a", "package"),
        "bundled-b": _skill("bundled-b", "package"),
    }
    # 白名单命中：包内只留命中项
    kept = filter_skills_by_whitelist(skills, ["bundled-a"])
    assert list(kept.keys()) == ["bundled-a"]


def test_exclude_prefix_package_skills():
    skills = {
        "bundled-a": _skill("bundled-a", "package"),
        "bundled-b": _skill("bundled-b", "package"),
        "user": _skill("user", "auto"),
    }
    # 纯排除名单：包内全放减排除；用户级不受影响
    kept = filter_skills_by_whitelist(skills, ["!bundled-b"])
    assert sorted(kept.keys()) == ["bundled-a", "user"]


def test_non_package_skills_bypass_whitelist():
    skills = {
        "pkg": _skill("pkg", "package"),
        "user": _skill("user", "auto"),
        "project": _skill("project", "top-level"),
        "explicit": _skill("explicit", "local"),
        "unknown": _skill("unknown", None),
    }
    # 非空白名单未点名包内 skill：包内被裁，其余来源全部放行
    kept = filter_skills_by_whitelist(skills, ["some-other-skill"])
    assert sorted(kept.keys()) == ["explicit", "project", "unknown", "user"]


def test_whitelist_still_applies_to_package_mixed_with_open():
    skills = {
        "pkg-allowed": _skill("pkg-allowed", "package"),
        "pkg-denied": _skill("pkg-denied", "package"),
        "user": _skill("user", "auto"),
    }
    kept = filter_skills_by_whitelist(skills, ["pkg-allowed"])
    assert sorted(kept.keys()) == ["pkg-allowed", "user"]
