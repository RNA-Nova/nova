"""名单代数——全部资源黑白名单的单一事实源（名字级）。

与收集层的路径级代数（``package/resolve/discovery.py::apply_patterns``）
**同一语法、两个作用域**：那边打在文件路径上（glob + 精确强制级），本模块
打在注册名上（全部精确匹配——名字空间平且小，glob 无的放矢；将来命名族
出现时把精确比较换 fnmatch 即可，代数骨架不变）。两实现语义逐条对齐，
语义用例双跑防漂移（``tests/core/utils/test_name_sets.py``）。

**三态语义**（``Optional[List[str]]`` 字段的判读规则）：

- ``None``（字段缺席/未声明）= **不设防**——该层不参与收窄；
- ``[]``（显式空列表）= **全禁**——该层把集合裁为空；
- ``[names]`` = 名单——按下列词汇求值。

**条目词汇**（四级，与路径级一致的优先级格）：

- ``"name"``  包含：名单含包含项时，未列名者出局（无包含项 = 从全集出发）；
- ``"!name"`` 排除：从结果中打洞；
- ``"+name"`` 强制包含：把被 ``!`` 裁掉的精确名加回来（压 ``!``）；
- ``"-name"`` 强制排除：终杀（压 ``+``）。

强制级是**单名单内**"宽排除 + 精确豁免"的支撑（与 pi 订正后语义一致），
不是跨层覆盖机制。层叠只有收窄语义（各层求交），层序无关——任何一层
都不能复活被上游裁掉的名字（信任边界的不可逆性是结构保证而非约定）。
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Set, Tuple

from nova_harness.core.types.resources.selection import CapabilitySelection

EXCLUDE_PREFIX = "!"
FORCE_INCLUDE_PREFIX = "+"
FORCE_EXCLUDE_PREFIX = "-"


def split_patterns(
    patterns: Iterable[str],
) -> Tuple[Set[str], Set[str], Set[str], Set[str]]:
    """把名单条目拆为（包含, 排除, 强制包含, 强制排除）四集，前缀已剥离。"""
    includes: Set[str] = set()
    excludes: Set[str] = set()
    force_includes: Set[str] = set()
    force_excludes: Set[str] = set()
    for item in patterns:
        if item.startswith(FORCE_INCLUDE_PREFIX):
            value = item[1:].strip()
            if value:
                force_includes.add(value)
        elif item.startswith(FORCE_EXCLUDE_PREFIX):
            value = item[1:].strip()
            if value:
                force_excludes.add(value)
        elif item.startswith(EXCLUDE_PREFIX):
            value = item[1:].strip()
            if value:
                excludes.add(value)
        else:
            value = item.strip()
            if value:
                includes.add(value)
    return includes, excludes, force_includes, force_excludes


def apply_name_list(
    candidates: Iterable[str], name_list: Optional[List[str]]
) -> Set[str]:
    """对候选名集合应用一层名单（三态 + 四级条目）。

    求值序（pi ``applyPatterns`` 对位）：包含（无包含项 = 全集）→ 扣 ``!``
    → 补 ``+``（从 *candidates* 全集找回）→ 扣 ``-``。
    """
    names = set(candidates)
    if name_list is None:
        return names
    if len(name_list) == 0:
        return set()
    includes, excludes, force_includes, force_excludes = split_patterns(name_list)
    # 注意复制：直接引用 names 会让 -= 原地修改污染全集，+ 复活将找不到名
    base = (names & includes) if includes else set(names)
    base -= excludes
    base |= names & force_includes
    base -= force_excludes
    return base


def resolve_name_set(
    candidates: Iterable[str],
    *layers: Optional[List[str]],
) -> Set[str]:
    """叠加多层名单，返回最终名集合（各层只能收窄，层序无关）。"""
    result = set(candidates)
    for layer in layers:
        result &= apply_name_list(result, layer)
    return result


def unmatched_entries(
    candidates: Iterable[str], name_list: Optional[List[str]]
) -> List[str]:
    """返回名单中未命中任何候选的条目（零命中诊断——拼写错误的显眼化）。

    仅报告"指向型"条目（包含/排除/强制包含/强制排除都查）；``None``/空名单
    无条目可报。供各裁决点在产出注册表时生成 ``ResourceDiagnostic``。
    """
    if not name_list:
        return []
    names = set(candidates)
    includes, excludes, force_includes, force_excludes = split_patterns(name_list)
    missing: List[str] = []
    for value in includes:
        if value not in names:
            missing.append(value)
    for value in excludes:
        if value not in names:
            missing.append(EXCLUDE_PREFIX + value)
    for value in force_includes:
        if value not in names:
            missing.append(FORCE_INCLUDE_PREFIX + value)
    for value in force_excludes:
        if value not in names:
            missing.append(FORCE_EXCLUDE_PREFIX + value)
    return missing


def is_name_allowed(name: str, name_list: Optional[List[str]]) -> bool:
    """单名判定（精确匹配下逐名求值 ≡ 集合求值的单点形式）。

    供"宇宙在调用方手里"的逐名过滤场景（如命令注册表逐个判定）。
    """
    return name in apply_name_list([name], name_list)


def build_selection_report(
    resource_type: str,
    name_list: Optional[List[str]],
    candidates: Iterable[str],
    surviving_after_settings: Optional[Set[str]] = None,
    surviving_after_sdk: Optional[Set[str]] = None,
    final: Optional[Set[str]] = None,
) -> List[CapabilitySelection]:
    """产出 yaml 名单点名项的 ``CapabilitySelection`` 报告（各资源裁决点共用）。

    只对**正向包含项**出报告（``!``/``+``/``-`` 前缀条目是修饰项，不出报告）。
    判定优先级（每层只归因一次）：

    1. 在 *final*（最终生效集）→ ``ok``；
    2. 不在 *candidates*（加载后全集/注册表键）→ ``missing``；
    3. settings 层后不在 → ``disabled_by_settings``；
    4. SDK 层后不在 → ``disabled_by_sdk``；
    5. 其余（穿过了 settings/SDK 却不在 final——被 yaml 自身排除项覆盖等）
       不出报告。

    参数说明：

    - *name_list*：yaml 名单原文（含前缀）；``None``（未声明）无点名项，
      返回空列表；
    - *surviving_after_settings* / *surviving_after_sdk*：对应层后的存活集；
      ``None`` 表示该层信息不可知（如路径级 settings 无法干净映射回注册名），
      跳过对应判定；
    - *final*：``None`` 时退化为 *candidates*（假设全集生效）。
    """
    if name_list is None:
        return []
    includes, _excludes, _force_includes, _force_excludes = split_patterns(name_list)
    candidate_set = set(candidates)
    final_set = candidate_set if final is None else set(final)

    report: List[CapabilitySelection] = []
    for name in sorted(includes):
        if name in final_set:
            status = "ok"
        elif name not in candidate_set:
            status = "missing"
        elif (
            surviving_after_settings is not None
            and name not in surviving_after_settings
        ):
            status = "disabled_by_settings"
        elif surviving_after_sdk is not None and name not in surviving_after_sdk:
            status = "disabled_by_sdk"
        else:
            continue
        report.append(
            CapabilitySelection(resource_type=resource_type, name=name, status=status)
        )
    return report


__all__ = [
    "EXCLUDE_PREFIX",
    "FORCE_EXCLUDE_PREFIX",
    "FORCE_INCLUDE_PREFIX",
    "apply_name_list",
    "build_selection_report",
    "is_name_allowed",
    "resolve_name_set",
    "split_patterns",
    "unmatched_entries",
]
