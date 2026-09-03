"""npm 源测试：spec 解析 / semver range 匹配 / resolver 下载链路 / updates 检查。"""

import base64
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest
from nova_harness.core.package.source._semver import (
    NpmRange,
    NpmRangeUnion,
    Version,
    max_satisfying,
    parse_version,
    parse_version_spec,
)
from nova_harness.core.package.source.resolver import SourceResolver
from nova_harness.core.package.source.spec import parse_source

# ---------------------------------------------------------------------------
# spec 解析
# ---------------------------------------------------------------------------


def test_npm_spec_exact_version():
    s = parse_source("npm:@scope/pkg@1.2.0")
    assert s.type == "npm"
    assert s.npm_name == "@scope/pkg"
    assert s.npm_version == "1.2.0"


def test_npm_spec_exact_version_with_prerelease_and_build():
    assert parse_source("npm:pkg@1.2.0-beta.1").npm_version == "1.2.0-beta.1"
    assert parse_source("npm:pkg@1.2.0+build.5").npm_version == "1.2.0+build.5"


def test_npm_spec_no_version_is_latest():
    s = parse_source("npm:pkg")
    assert s.type == "npm"
    assert s.npm_name == "pkg"
    assert s.npm_version is None

    s2 = parse_source("npm:pkg@latest")
    assert s2.npm_version is None


def test_npm_spec_caret_range():
    s = parse_source("npm:pkg@^1.2.0")
    assert s.npm_version == "^1.2.0"

    s2 = parse_source("npm:@scope/pkg@^0.2.3")
    assert s2.npm_name == "@scope/pkg"
    assert s2.npm_version == "^0.2.3"


def test_npm_spec_tilde_range():
    assert parse_source("npm:pkg@~1.2.3").npm_version == "~1.2.3"
    assert parse_source("npm:pkg@~1.2").npm_version == "~1.2"
    assert parse_source("npm:pkg@~1").npm_version == "~1"


def test_npm_spec_partial_version():
    assert parse_source("npm:pkg@1").npm_version == "1"
    assert parse_source("npm:pkg@1.2").npm_version == "1.2"


def test_npm_spec_range_with_prerelease():
    assert parse_source("npm:pkg@^1.2.3-beta").npm_version == "^1.2.3-beta"


def test_npm_spec_wildcard_any_accepted():
    # * / x / X 单独使用 = 任意版本（max satisfying 取最高非 prerelease）
    assert parse_source("npm:pkg@*").npm_version == "*"
    assert parse_source("npm:pkg@x").npm_version == "x"
    assert parse_source("npm:pkg@X").npm_version == "X"


def test_npm_spec_wildcard_segments():
    assert parse_source("npm:pkg@1.2.x").npm_version == "1.2.x"
    assert parse_source("npm:pkg@1.2.*").npm_version == "1.2.*"
    assert parse_source("npm:pkg@1.x").npm_version == "1.x"


def test_npm_spec_v_prefix():
    # v 前缀容忍（归一发生在解析期；spec 字符串原样保留）
    assert parse_source("npm:pkg@v1.2.3").npm_version == "v1.2.3"


def test_npm_spec_comparator_set():
    s = parse_source("npm:pkg@>=1.2.0 <2.0.0")
    assert s.npm_version == ">=1.2.0 <2.0.0"
    assert parse_source("npm:pkg@>1.0.0 <=1.5.0").npm_version == ">1.0.0 <=1.5.0"
    assert parse_source("npm:pkg@=1.2.3").npm_version == "=1.2.3"
    # 比较器混部分版本
    assert parse_source("npm:pkg@>=1.2 <2").npm_version == ">=1.2 <2"


def test_npm_spec_union():
    s = parse_source("npm:pkg@^1.2.0 || ^2.0.0")
    assert s.npm_version == "^1.2.0 || ^2.0.0"
    assert parse_source("npm:@scope/pkg@1.2.3 || >=2.0.0").npm_name == "@scope/pkg"


def test_npm_spec_hyphen_range():
    assert parse_source("npm:pkg@1.2.3 - 2.3.4").npm_version == "1.2.3 - 2.3.4"
    assert parse_source("npm:pkg@1.2 - 2.3").npm_version == "1.2 - 2.3"


def test_npm_spec_bad_version_rejected():
    """语义非法且不可能被当作 dist-tag 的 spec（操作符/空格/混排）仍在解析期拒绝。"""
    for bad in (
        "^",
        "~",
        "^x.1",  # 通配段后不允许再出现数字段
        ">=",  # 操作符缺版本体
        ">= 1.2.0",  # 操作符与版本之间不允许空格
        "1.2 - 2.3.4 - 3.0.0",  # hyphen 只允许两个端点
        ">=1.0.0 1.2.3 - 2.0.0",  # hyphen 不支持与其他比较器混排
        "1.2.3 -",  # hyphen 缺右端点
    ):
        with pytest.raises(ValueError, match="Invalid npm version spec or dist-tag"):
            parse_source(f"npm:pkg@{bad}")


def test_npm_spec_dist_tag_accepted():
    """非版本语法的字符串按 dist-tag 接受（npm 对齐——tag 存在性交由
    resolver 查询 registry 时校验，解析期不再误报"版本语法错误"）。"""
    for tag in ("abc", "beta", "next", "rc-1", "v", "1.2.3.4", "1.x.3", "1.2.x-beta"):
        source = parse_source(f"npm:pkg@{tag}")
        assert source.npm_version == tag


# ---------------------------------------------------------------------------
# _semver 单元测试：版本比较与 range 匹配
# ---------------------------------------------------------------------------


def test_semver_version_ordering():
    # 数值比较而非字典序
    assert parse_version("1.10.0") > parse_version("1.9.0")
    assert parse_version("2.0.0") > parse_version("1.99.99")
    # prerelease 低于同三元组正式版
    assert parse_version("1.0.0-alpha") < parse_version("1.0.0")
    # prerelease 段比较：数值段按数值、数字段低于字母段、字段多者更高
    assert parse_version("1.0.0-alpha") < parse_version("1.0.0-alpha.1")
    assert parse_version("1.0.0-alpha.1") < parse_version("1.0.0-alpha.beta")
    assert parse_version("1.0.0-beta.2") < parse_version("1.0.0-beta.11")
    # build metadata 不参与 precedence
    assert parse_version("1.0.0+build.1") == parse_version("1.0.0")


def test_semver_parse_version_rejects_invalid():
    for bad in ("", "1", "1.2", "1.2.3.4", "abc"):
        with pytest.raises(ValueError):
            parse_version(bad)


def _allows(spec: str, version: str) -> bool:
    parsed = parse_version_spec(spec)
    assert isinstance(parsed, NpmRange)
    return parsed.allows(parse_version(version))


def test_semver_caret_range_bounds():
    assert _allows("^1.2.3", "1.2.3")
    assert _allows("^1.2.3", "1.9.9")
    assert not _allows("^1.2.3", "2.0.0")
    assert not _allows("^1.2.3", "1.2.2")
    # 0.x：锁定最左非零位
    assert _allows("^0.2.3", "0.2.9")
    assert not _allows("^0.2.3", "0.3.0")
    assert _allows("^0.0.3", "0.0.3")
    assert not _allows("^0.0.3", "0.0.4")
    # 部分版本：^1.2 := >=1.2.0 <2.0.0；^1 := >=1.0.0 <2.0.0
    assert _allows("^1.2", "1.9.0")
    assert not _allows("^1.2", "1.1.9")
    assert _allows("^1", "1.9.9")
    assert not _allows("^1", "2.0.0")


def test_semver_tilde_range_bounds():
    assert _allows("~1.2.3", "1.2.9")
    assert not _allows("~1.2.3", "1.3.0")
    assert not _allows("~1.2.3", "1.2.2")
    assert _allows("~1.2", "1.2.0")
    assert not _allows("~1.2", "1.3.0")
    assert _allows("~1", "1.9.9")
    assert not _allows("~1", "2.0.0")


def test_semver_partial_version_bounds():
    # 裸部分版本 ≡ npm x-range（``1`` ≡ ``1.x``，``1.2`` ≡ ``1.2.x``）
    assert _allows("1", "1.9.9")
    assert not _allows("1", "2.0.0")
    assert _allows("1.2", "1.2.9")
    assert not _allows("1.2", "1.3.0")


def test_semver_range_prerelease_policy():
    # spec 不带 prerelease：所有 prerelease 候选被排除（即便落在区间内）
    assert not _allows("^1.2.0", "1.3.0-beta")
    assert _allows("^1.2.0", "1.3.0")
    # spec 带 prerelease：仅放行同三元组的 prerelease 候选
    assert _allows("^1.2.3-beta", "1.2.3-rc.1")
    assert _allows("^1.2.3-beta", "1.2.3")
    assert not _allows("^1.2.3-beta", "1.2.4-beta")
    # 同三元组但低于下界 prerelease 的仍不满足
    assert not _allows("^1.2.3-beta", "1.2.3-alpha")


def test_semver_exact_spec_is_not_range():
    parsed = parse_version_spec("1.2.3")
    assert isinstance(parsed, Version)
    parsed_pre = parse_version_spec("1.2.3-beta.1")
    assert isinstance(parsed_pre, Version)
    assert parsed_pre.prerelease == ("beta", "1")


def test_semver_max_satisfying():
    parsed = parse_version_spec("^1.0.0")
    assert isinstance(parsed, NpmRange)
    # 跳过无法解析的候选；按 semver 而非字典序取最大
    assert max_satisfying(["1.9.0", "not-a-version", "1.10.0"], parsed) == "1.10.0"
    assert max_satisfying(["2.0.0"], parsed) is None
    assert max_satisfying([], parsed) is None


def test_semver_any_range_bounds():
    # * / x / X = 任意版本（无界区间；prerelease 候选仍被排除）
    for spec in ("*", "x", "X"):
        parsed = parse_version_spec(spec)
        assert isinstance(parsed, NpmRange)
        assert parsed.lower is None and parsed.upper is None
        assert _allows(spec, "0.0.1")
        assert _allows(spec, "99.99.99")
        assert not _allows(spec, "1.0.0-beta")


def test_semver_xrange_bounds():
    # 段级通配按 x-range 展开
    assert _allows("1.2.x", "1.2.0")
    assert _allows("1.2.x", "1.2.9")
    assert not _allows("1.2.x", "1.3.0")
    assert _allows("1.2.*", "1.2.9")
    assert not _allows("1.2.*", "1.2.0-alpha")
    assert _allows("1.x", "1.9.9")
    assert not _allows("1.x", "2.0.0")
    assert _allows("1.X", "1.0.0")
    # ^/~ 同样接受通配段：^1.2.x := >=1.2.0 <2.0.0；~1.x := >=1.0.0 <2.0.0
    assert _allows("^1.2.x", "1.9.0")
    assert not _allows("^1.2.x", "2.0.0")
    assert not _allows("^1.2.x", "1.1.9")
    assert _allows("~1.x", "1.9.9")
    assert not _allows("~1.x", "2.0.0")
    # ^* / ~x = 任意版本
    assert _allows("^*", "3.1.4")
    assert _allows("~x", "3.1.4")


def test_semver_v_prefix():
    # v 前缀容忍归一：精确版返回 Version
    parsed = parse_version_spec("v1.2.3")
    assert isinstance(parsed, Version)
    assert parsed == Version(1, 2, 3)
    parsed_upper = parse_version_spec("V1.2.3")
    assert isinstance(parsed_upper, Version)
    assert parsed_upper == Version(1, 2, 3)
    # range 语境下同样容忍
    assert _allows("^v1.2.0", "1.5.0")
    assert not _allows("^v1.2.0", "2.0.0")
    assert _allows("=v1.2.3", "1.2.3")
    assert not _allows("=v1.2.3", "1.2.4")


def test_semver_comparator_set_bounds():
    # 空格分隔 = AND 交集
    assert _allows(">=1.2.0 <2.0.0", "1.2.0")
    assert _allows(">=1.2.0 <2.0.0", "1.9.9")
    assert not _allows(">=1.2.0 <2.0.0", "2.0.0")
    assert not _allows(">=1.2.0 <2.0.0", "1.1.9")
    # > / <= 的边界开闭
    assert not _allows(">1.0.0 <=1.5.0", "1.0.0")
    assert _allows(">1.0.0 <=1.5.0", "1.0.1")
    assert _allows(">1.0.0 <=1.5.0", "1.5.0")
    assert not _allows(">1.0.0 <=1.5.0", "1.5.1")
    # = 精确钉版
    assert _allows("=1.2.3", "1.2.3")
    assert not _allows("=1.2.3", "1.2.4")
    assert not _allows("=1.2.3", "1.2.3-beta")
    # 交集为空（>2.0.0 <1.0.0）：解析不报错，但不匹配任何版本
    assert not _allows(">2.0.0 <1.0.0", "1.5.0")
    assert not _allows(">2.0.0 <1.0.0", "0.5.0")
    # 比较器集的 prerelease 参与规则与 ^/~ 一致
    assert not _allows(">=1.2.0 <2.0.0", "1.5.0-beta")
    assert _allows(">=1.2.3-beta <2.0.0", "1.2.3-rc.1")
    assert not _allows(">=1.2.3-beta <2.0.0", "1.2.4-beta")


def test_semver_comparator_partial_versions():
    # npm 部分版本比较器展开规则
    assert _allows(">=1.2", "1.2.0")  # >=1.2 := >=1.2.0
    assert not _allows(">=1.2", "1.1.9")
    assert not _allows("<1.2", "1.2.0")  # <1.2 := <1.2.0（不含 1.2.0 自身）
    assert _allows("<1.2", "1.1.9")
    assert not _allows(">1.2", "1.2.9")  # >1.2 := >=1.3.0
    assert _allows(">1.2", "1.3.0")
    assert _allows("<=1.2", "1.2.9")  # <=1.2 := <1.3.0
    assert not _allows("<=1.2", "1.3.0")
    assert _allows("=1.2", "1.2.9")  # =1.2 := >=1.2.0 <1.3.0
    assert not _allows("=1.2", "1.3.0")
    # 单段同理
    assert _allows(">1", "2.0.0")
    assert not _allows(">1", "1.9.9")
    assert _allows("<=1", "1.9.9")
    assert not _allows("<=1", "2.0.0")
    # 通配段与部分版本同义
    assert _allows(">=1.2.x", "1.2.0")
    assert not _allows("<1.2.*", "1.2.0")


def test_semver_union_bounds():
    parsed = parse_version_spec("^1.2.0 || ^2.0.0")
    assert isinstance(parsed, NpmRangeUnion)

    def allows(version: str) -> bool:
        return parsed.allows(parse_version(version))

    assert allows("1.2.0")
    assert allows("1.9.0")
    assert allows("2.5.0")
    assert not allows("3.0.0")
    assert not allows("1.1.0")
    # prerelease 候选跨分支同样被排除
    assert not allows("2.5.0-beta")
    # 并集分支可混裸精确版本与比较器
    mixed = parse_version_spec("1.2.3 || >=2.0.0")
    assert isinstance(mixed, NpmRangeUnion)
    assert mixed.allows(parse_version("1.2.3"))
    assert not mixed.allows(parse_version("1.2.4"))
    assert mixed.allows(parse_version("2.5.0"))
    # 带 prerelease 的分支放行同三元组候选
    pre = parse_version_spec("^1.2.3-beta || ^2.0.0")
    assert pre.allows(parse_version("1.2.3-rc.1"))
    assert not pre.allows(parse_version("1.2.4-beta"))


def test_semver_hyphen_range_bounds():
    # 完整端点：>=1.2.3 <=2.3.4（右端点闭区间）
    assert _allows("1.2.3 - 2.3.4", "1.2.3")
    assert _allows("1.2.3 - 2.3.4", "2.0.0")
    assert _allows("1.2.3 - 2.3.4", "2.3.4")
    assert not _allows("1.2.3 - 2.3.4", "2.3.5")
    assert not _allows("1.2.3 - 2.3.4", "1.2.2")
    # 部分版本端点：左端点补零、右端点上界提升（1.2 - 2.3 := >=1.2.0 <2.4.0）
    assert _allows("1.2 - 2.3", "1.2.0")
    assert _allows("1.2 - 2.3", "2.3.9")
    assert not _allows("1.2 - 2.3", "2.4.0")
    assert not _allows("1.2 - 2.3", "1.1.9")
    # 单段端点：1 - 2 := >=1.0.0 <3.0.0
    assert _allows("1 - 2", "1.0.0")
    assert _allows("1 - 2", "2.9.9")
    assert not _allows("1 - 2", "3.0.0")
    # prerelease 候选默认排除
    assert not _allows("1.2.3 - 2.3.4", "2.0.0-beta")


def test_semver_max_satisfying_any_and_union():
    # 任意版本：取最高非 prerelease
    any_range = parse_version_spec("*")
    assert max_satisfying(["1.0.0", "2.0.0-beta", "1.5.0"], any_range) == "1.5.0"
    assert max_satisfying(["2.0.0-beta"], any_range) is None
    # 并集：跨分支取最大满足版本
    union = parse_version_spec("^1.0.0 || ^2.0.0")
    assert max_satisfying(["1.5.0", "2.3.0", "3.0.0"], union) == "2.3.0"
    assert max_satisfying(["3.0.0"], union) is None


# ---------------------------------------------------------------------------
# resolver 下载链路（mock 网络层）
# ---------------------------------------------------------------------------


def _make_tarball(files: dict[str, str]) -> bytes:
    """构造 npm tarball（package/ 前缀的 tar.gz）。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=f"package/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _metadata_for(name: str, version: str, tarball_url: str, payload: bytes) -> dict:
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(payload).digest()).decode()
    return {
        "dist-tags": {"latest": version},
        "versions": {
            version: {"dist": {"tarball": tarball_url, "integrity": integrity}}
        },
    }


def _metadata_with_versions(
    name: str, versions: list[str], latest: str
) -> tuple[dict, dict[str, bytes]]:
    """构造多版本 registry metadata；返回 ``(metadata, payloads)``。

    每个版本的 tarball 内 package.json 携带自身版本号，便于安装后断言实际
    选中的版本；payloads 以 tarball URL 为键，供 fake urlopen 按 URL 分发。
    """
    payloads: dict[str, bytes] = {}
    version_entries: dict[str, dict] = {}
    for v in versions:
        payload = _make_tarball(
            {"package.json": json.dumps({"name": name, "version": v})}
        )
        url = f"https://reg/{name}-{v}.tgz"
        payloads[url] = payload
        integrity = (
            "sha512-" + base64.b64encode(hashlib.sha512(payload).digest()).decode()
        )
        version_entries[v] = {"dist": {"tarball": url, "integrity": integrity}}
    return {"dist-tags": {"latest": latest}, "versions": version_entries}, payloads


class _FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _resolve_installed_version(
    tmp_path: Path, spec: str, metadata: dict, payloads: dict[str, bytes]
) -> str:
    """以 mock 网络层解析并安装 *spec*，返回实际安装的版本号。"""
    resolver = SourceResolver(agent_dir=tmp_path)
    with (
        patch(
            "nova_harness.core.package.source.resolver.npm_fetch_json",
            return_value=metadata,
        ),
        patch(
            "nova_harness.core.package.source.resolver.urllib.request.urlopen",
            side_effect=lambda url, timeout=None: _FakeResponse(payloads[url]),
        ),
    ):
        path = resolver.resolve(parse_source(spec), update=True)
    pkg_json = json.loads((Path(path) / "package.json").read_text(encoding="utf-8"))
    return pkg_json["version"]


def test_npm_resolve_downloads_verifies_and_caches(tmp_path: Path):
    payload = _make_tarball({"package.json": '{"name":"pkg","version":"1.0.0"}'})
    metadata = _metadata_for("pkg", "1.0.0", "https://reg/pkg.tgz", payload)

    resolver = SourceResolver(agent_dir=tmp_path)
    source = parse_source("npm:pkg")  # latest

    with (
        patch(
            "nova_harness.core.package.source.resolver.npm_fetch_json",
            return_value=metadata,
        ),
        patch(
            "nova_harness.core.package.source.resolver.urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ),
    ):
        path = resolver.resolve(source, update=True)

    assert (Path(path) / "package.json").exists()
    # 缓存即安装态：只读解析（不触网）直接命中
    assert resolver.resolve(source, update=False) == path


def test_npm_resolve_integrity_mismatch_rejected(tmp_path: Path):
    payload = _make_tarball({"package.json": "{}"})
    metadata = _metadata_for("pkg", "1.0.0", "https://reg/pkg.tgz", payload)
    # 篡改 integrity
    metadata["versions"]["1.0.0"]["dist"]["integrity"] = (
        "sha512-" + base64.b64encode(b"0" * 64).decode()
    )

    resolver = SourceResolver(agent_dir=tmp_path)
    with (
        patch(
            "nova_harness.core.package.source.resolver.npm_fetch_json",
            return_value=metadata,
        ),
        patch(
            "nova_harness.core.package.source.resolver.urllib.request.urlopen",
            return_value=_FakeResponse(payload),
        ),
    ):
        with pytest.raises(ValueError, match="integrity mismatch"):
            resolver.resolve(parse_source("npm:pkg@1.0.0"), update=True)


def test_npm_resolve_missing_cache_readonly_errors(tmp_path: Path):
    resolver = SourceResolver(agent_dir=tmp_path)
    with pytest.raises(ValueError, match="not installed"):
        resolver.resolve(parse_source("npm:pkg"), update=False)


def test_npm_resolve_offline_rejected(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("NOVA_OFFLINE", "1")
    resolver = SourceResolver(agent_dir=tmp_path)
    with pytest.raises(ValueError, match="offline"):
        resolver.resolve(parse_source("npm:pkg"), update=True)


def test_npm_resolve_version_not_found(tmp_path: Path):
    payload = _make_tarball({})
    metadata = _metadata_for("pkg", "1.0.0", "https://reg/pkg.tgz", payload)
    resolver = SourceResolver(agent_dir=tmp_path)
    with patch(
        "nova_harness.core.package.source.resolver.npm_fetch_json",
        return_value=metadata,
    ):
        with pytest.raises(ValueError, match="version not found"):
            resolver.resolve(parse_source("npm:pkg@9.9.9"), update=True)


# ---------------------------------------------------------------------------
# resolver range 求值（max satisfying）
# ---------------------------------------------------------------------------


def test_npm_resolve_caret_picks_max_satisfying(tmp_path: Path):
    metadata, payloads = _metadata_with_versions(
        "pkg", ["1.2.0", "1.5.0", "2.0.0"], latest="2.0.0"
    )
    assert (
        _resolve_installed_version(tmp_path, "npm:pkg@^1.2.0", metadata, payloads)
        == "1.5.0"
    )


def test_npm_resolve_caret_zero_major_lock(tmp_path: Path):
    # ^0.2.3 := >=0.2.3 <0.3.0（0.x 锁定最左非零位）
    metadata, payloads = _metadata_with_versions(
        "pkg", ["0.2.3", "0.2.9", "0.3.0"], latest="0.3.0"
    )
    assert (
        _resolve_installed_version(tmp_path, "npm:pkg@^0.2.3", metadata, payloads)
        == "0.2.9"
    )
    # ^0.0.3 := >=0.0.3 <0.0.4
    metadata, payloads = _metadata_with_versions(
        "pkg", ["0.0.3", "0.0.4"], latest="0.0.4"
    )
    assert (
        _resolve_installed_version(tmp_path, "npm:pkg@^0.0.3", metadata, payloads)
        == "0.0.3"
    )


def test_npm_resolve_tilde_picks_max_satisfying(tmp_path: Path):
    metadata, payloads = _metadata_with_versions(
        "pkg", ["1.2.0", "1.2.9", "1.3.0"], latest="1.3.0"
    )
    assert (
        _resolve_installed_version(tmp_path, "npm:pkg@~1.2.0", metadata, payloads)
        == "1.2.9"
    )


def test_npm_resolve_partial_version(tmp_path: Path):
    metadata, payloads = _metadata_with_versions(
        "pkg", ["1.9.0", "2.0.0"], latest="2.0.0"
    )
    assert (
        _resolve_installed_version(tmp_path, "npm:pkg@1", metadata, payloads) == "1.9.0"
    )
    metadata, payloads = _metadata_with_versions(
        "pkg", ["1.2.7", "1.3.0"], latest="1.3.0"
    )
    assert (
        _resolve_installed_version(tmp_path, "npm:pkg@1.2", metadata, payloads)
        == "1.2.7"
    )


def test_npm_resolve_max_satisfying_is_semver_not_lexicographic(tmp_path: Path):
    # 字典序下 "1.9.0" > "1.10.0"，semver precedence 下反之
    metadata, payloads = _metadata_with_versions(
        "pkg", ["1.9.0", "1.10.0"], latest="1.10.0"
    )
    assert (
        _resolve_installed_version(tmp_path, "npm:pkg@^1.0.0", metadata, payloads)
        == "1.10.0"
    )


def test_npm_resolve_range_excludes_prerelease(tmp_path: Path):
    metadata, payloads = _metadata_with_versions(
        "pkg", ["1.2.0", "1.5.0-beta"], latest="1.2.0"
    )
    assert (
        _resolve_installed_version(tmp_path, "npm:pkg@^1.0.0", metadata, payloads)
        == "1.2.0"
    )


def test_npm_resolve_range_with_prerelease_allows_same_triple(tmp_path: Path):
    metadata, payloads = _metadata_with_versions(
        "pkg", ["1.9.0", "2.0.0-alpha", "2.0.0-rc.1", "2.1.0-beta"], latest="1.9.0"
    )
    # 2.1.0-beta 与 spec 三元组不同被排除；同三元组 prerelease 参与取最大
    assert (
        _resolve_installed_version(tmp_path, "npm:pkg@^2.0.0-beta", metadata, payloads)
        == "2.0.0-rc.1"
    )


def test_npm_resolve_range_no_match_error(tmp_path: Path):
    metadata, _ = _metadata_with_versions("pkg", ["2.0.0"], latest="2.0.0")
    resolver = SourceResolver(agent_dir=tmp_path)
    with patch(
        "nova_harness.core.package.source.resolver.npm_fetch_json",
        return_value=metadata,
    ):
        with pytest.raises(ValueError, match="No npm version satisfies") as exc_info:
            resolver.resolve(parse_source("npm:pkg@^3.0.0"), update=True)
    # 错误信息需携带 range 与可用最新版
    assert "^3.0.0" in str(exc_info.value)
    assert "2.0.0" in str(exc_info.value)


def test_npm_resolve_exact_prerelease_direct_hit(tmp_path: Path):
    # 精确版指定 prerelease：不走 range 匹配，按原逻辑直取
    metadata, payloads = _metadata_with_versions(
        "pkg", ["1.0.0-beta", "1.0.0"], latest="1.0.0"
    )
    assert (
        _resolve_installed_version(tmp_path, "npm:pkg@1.0.0-beta", metadata, payloads)
        == "1.0.0-beta"
    )


def test_npm_resolve_exact_pin_ignores_latest(tmp_path: Path):
    metadata, payloads = _metadata_with_versions(
        "pkg", ["1.0.0", "2.0.0"], latest="2.0.0"
    )
    assert (
        _resolve_installed_version(tmp_path, "npm:pkg@1.0.0", metadata, payloads)
        == "1.0.0"
    )


def test_npm_resolve_wildcard_picks_highest_stable(tmp_path: Path):
    # * = 任意版本：max satisfying 取最高非 prerelease（而非 dist-tags.latest）
    metadata, payloads = _metadata_with_versions(
        "pkg", ["1.2.0", "1.9.0", "2.0.0-beta"], latest="1.2.0"
    )
    assert (
        _resolve_installed_version(tmp_path, "npm:pkg@*", metadata, payloads) == "1.9.0"
    )


def test_npm_resolve_xrange(tmp_path: Path):
    metadata, payloads = _metadata_with_versions(
        "pkg", ["1.2.7", "1.3.0"], latest="1.3.0"
    )
    assert (
        _resolve_installed_version(tmp_path, "npm:pkg@1.2.x", metadata, payloads)
        == "1.2.7"
    )
    metadata, payloads = _metadata_with_versions(
        "pkg", ["1.9.0", "2.0.0"], latest="2.0.0"
    )
    assert (
        _resolve_installed_version(tmp_path, "npm:pkg@1.x", metadata, payloads)
        == "1.9.0"
    )


def test_npm_resolve_v_prefix_exact(tmp_path: Path):
    # v 前缀归一为精确版本直取
    metadata, payloads = _metadata_with_versions(
        "pkg", ["1.2.3", "1.3.0"], latest="1.3.0"
    )
    assert (
        _resolve_installed_version(tmp_path, "npm:pkg@v1.2.3", metadata, payloads)
        == "1.2.3"
    )


def test_npm_resolve_comparator_set(tmp_path: Path):
    metadata, payloads = _metadata_with_versions(
        "pkg", ["1.1.0", "1.5.0", "2.0.0"], latest="2.0.0"
    )
    assert (
        _resolve_installed_version(
            tmp_path, "npm:pkg@>=1.2.0 <2.0.0", metadata, payloads
        )
        == "1.5.0"
    )
    # > / <= 边界：排除 1.0.0、含 1.5.0
    metadata, payloads = _metadata_with_versions(
        "pkg", ["1.0.0", "1.5.0", "1.5.1"], latest="1.5.1"
    )
    assert (
        _resolve_installed_version(
            tmp_path, "npm:pkg@>1.0.0 <=1.5.0", metadata, payloads
        )
        == "1.5.0"
    )


def test_npm_resolve_equals_pin(tmp_path: Path):
    metadata, payloads = _metadata_with_versions(
        "pkg", ["1.2.3", "1.2.4"], latest="1.2.4"
    )
    assert (
        _resolve_installed_version(tmp_path, "npm:pkg@=1.2.3", metadata, payloads)
        == "1.2.3"
    )


def test_npm_resolve_union_picks_max_across_branches(tmp_path: Path):
    metadata, payloads = _metadata_with_versions(
        "pkg", ["1.9.0", "2.5.0", "3.0.0"], latest="3.0.0"
    )
    assert (
        _resolve_installed_version(
            tmp_path, "npm:pkg@^1.2.0 || ^2.0.0", metadata, payloads
        )
        == "2.5.0"
    )


def test_npm_resolve_union_no_match_error(tmp_path: Path):
    metadata, _ = _metadata_with_versions("pkg", ["2.0.0"], latest="2.0.0")
    resolver = SourceResolver(agent_dir=tmp_path)
    with patch(
        "nova_harness.core.package.source.resolver.npm_fetch_json",
        return_value=metadata,
    ):
        with pytest.raises(ValueError, match="No npm version satisfies") as exc_info:
            resolver.resolve(parse_source("npm:pkg@^3.0.0 || ^4.0.0"), update=True)
    assert "^3.0.0 || ^4.0.0" in str(exc_info.value)


def test_npm_resolve_hyphen_range(tmp_path: Path):
    # 完整端点：>=1.2.3 <=2.3.4（右端点闭区间）
    metadata, payloads = _metadata_with_versions(
        "pkg", ["1.2.3", "2.3.4", "2.3.5"], latest="2.3.5"
    )
    assert (
        _resolve_installed_version(
            tmp_path, "npm:pkg@1.2.3 - 2.3.4", metadata, payloads
        )
        == "2.3.4"
    )
    # 部分端点：1.2 - 2.3 := >=1.2.0 <2.4.0（右端点上界提升）
    metadata, payloads = _metadata_with_versions(
        "pkg", ["2.3.9", "2.4.0"], latest="2.4.0"
    )
    assert (
        _resolve_installed_version(tmp_path, "npm:pkg@1.2 - 2.3", metadata, payloads)
        == "2.3.9"
    )


# ---------------------------------------------------------------------------
# dist-tag 解析（npm:pkg@<tag>——beta/next/canary）
# ---------------------------------------------------------------------------


def _metadata_with_dist_tags(
    name: str, versions: list[str], tags: dict[str, str]
) -> tuple[dict, dict[str, bytes]]:
    """构造带自定义 dist-tags 的 metadata（tags 含 latest 与非 latest tag）。"""
    metadata, payloads = _metadata_with_versions(name, versions, tags["latest"])
    metadata["dist-tags"] = dict(tags)
    return metadata, payloads


def test_npm_resolve_dist_tag(tmp_path: Path):
    """npm:pkg@beta 经 dist-tags 解析为具体版本（非 latest 的 tag）。"""
    metadata, payloads = _metadata_with_dist_tags(
        "pkg", ["1.0.0", "2.0.0-beta.1"], {"latest": "1.0.0", "beta": "2.0.0-beta.1"}
    )
    assert (
        _resolve_installed_version(tmp_path, "npm:pkg@beta", metadata, payloads)
        == "2.0.0-beta.1"
    )


def test_npm_resolve_exact_version_not_shadowed_by_tags(tmp_path: Path):
    """精确版本直取不受 dist-tags 存在影响。"""
    metadata, payloads = _metadata_with_dist_tags(
        "pkg", ["1.0.0", "2.0.0-beta.1"], {"latest": "1.0.0", "beta": "2.0.0-beta.1"}
    )
    assert (
        _resolve_installed_version(tmp_path, "npm:pkg@2.0.0-beta.1", metadata, payloads)
        == "2.0.0-beta.1"
    )


def test_npm_resolve_unknown_tag_reports_available(tmp_path: Path):
    """未知 tag：报错列出可用 dist-tags（不再是裸语法错误）。"""
    metadata, payloads = _metadata_with_dist_tags(
        "pkg", ["1.0.0"], {"latest": "1.0.0", "beta": "1.0.0"}
    )
    resolver = SourceResolver(agent_dir=tmp_path)
    with (
        patch(
            "nova_harness.core.package.source.resolver.npm_fetch_json",
            return_value=metadata,
        ),
        pytest.raises(ValueError, match="dist-tag") as exc_info,
    ):
        resolver.resolve(parse_source("npm:pkg@nope"), update=True)
    assert "beta" in str(exc_info.value)
    assert "latest" in str(exc_info.value)
