"""权限套餐编译的路径件——纯计算 URI/约定词汇（零 I/O）。

对位 codex 锚点 39f4277087：
- `utils/path-uri/src/lib.rs`（`PathUri` / `PathConvention`）、
  `api_path_string.rs`（`LegacyAppPathString`）、`config_path.rs`
  （`resolve_config_path_uri` / `validate_*`）；
- `utils/absolute-path/src/lib.rs:159`（`normalize_windows_device_path`）；
- `config/src/path_context.rs`（`ConfigPathContext`）。

裁剪登记（相对锚点的刻意简化，全部 fail-closed 等价）：
- **opaque 兜底 URI 未移植**（codex `BAD_PATH_URI_PREFIX` base64 命名空间：
  承载非 UTF-8 / Windows 命名空间等无法普通拼写的路径）。本侧输入一律
  Python `str`，非法形状在解析/校验点直接报错——与 codex 的
  `validate_config_path` 拒绝点同效。
- **段内编码分隔符 fail-closed 提前**：codex 在 `containment_path_segments`
  处拒绝解码后含 `/`/`\\` 的段（`lexical_depth` 归 None）；本侧在解析时即
  标记 `has_separator_segment`，`starts_with`/`validate_config_path` 同样
  拒绝（对位 lib.rs:776-787 的语义，不放宽）。
- 非 UTF-8 字节序列：解析 `%XX` 时严格 UTF-8 解码，非法即拒（对位
  `validate_config_path` 的 `from_utf8` 检查，config_path.rs:104-113）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

#: Windows 双分隔符切分（对位 lib.rs:1028-1029 的 split closure）
_WINDOWS_SPLIT_RE = re.compile(r"[/\\]")


class PermissionPathError(ValueError):
    """路径解析/校验错误（对位 codex `PathUriParseError` /
    `LegacyAppPathStringError`——文案逐字对位，类别统一 ValueError 族）"""


class PermissionPathConvention(Enum):
    """路径语法约定（对位 codex `PathConvention`，lib.rs:993）"""

    POSIX = "posix"
    WINDOWS = "windows"

    def __str__(self) -> str:
        # 对位 lib.rs:1033-1040 的 Display（错误文案用）
        return "POSIX" if self is PermissionPathConvention.POSIX else "Windows"

    def home_relative_suffix(self, path: str) -> str | None:
        """`~` 相对home 的后缀（对位 lib.rs:1013-1019；带前导分隔符返回）"""
        if not path.startswith("~"):
            return None
        suffix = path[1:]
        if (
            suffix == ""
            or suffix.startswith("/")
            or (self is PermissionPathConvention.WINDOWS and suffix.startswith("\\"))
        ):
            return suffix
        return None

    def path_segments(self, path: str) -> list[str]:
        """词法分段（对位 lib.rs:1025-1030；POSIX 切 `/`，Windows 切 `\\` 与
        `/`，保留空段）"""
        if self is PermissionPathConvention.POSIX:
            return path.split("/")
        return _WINDOWS_SPLIT_RE.split(path)


def normalize_windows_device_path(path: str) -> str | None:
    """Windows 驱动器/UNC 命名空间别名归一（逐行对位
    utils/absolute-path/src/lib.rs:159-180 `normalize_windows_device_path`）"""
    # 前缀字面量（Python 源写法 → 实际字符）："\\\\?\\UNC\\" = \\?\UNC\
    for prefix in ("\\\\?\\UNC\\", "\\\\.\\UNC\\"):
        if path.startswith(prefix):
            return "\\\\" + path[len(prefix) :]
    for prefix in ("\\\\?\\", "\\\\.\\"):
        rest = path[len(prefix) :] if path.startswith(prefix) else None
        if rest is not None and _is_windows_drive_absolute_path(rest):
            return rest
    return None


def _is_windows_drive_absolute_path(path: str) -> bool:
    """对位 lib.rs:183-190 `is_windows_drive_absolute_path`"""
    return (
        len(path) >= 3
        and path[0].isascii()
        and path[0].isalpha()
        and path[1] == ":"
        and path[2] in "\\/"
    )


def _is_windows_drive_segment(segment: str) -> bool:
    """驱动器段形状（对位 lib.rs:769-774 `is_windows_drive_uri_segment`——
    本侧段已解码，`%3A` 形态在解码后即 `C:`）"""
    return (
        len(segment) == 2
        and segment[0].isascii()
        and segment[0].isalpha()
        and segment[1] == ":"
    )


def _decode_segment(segment: str, raw_uri: str) -> str:
    """百分号解码单个 URI 段（严格 UTF-8——非法序列即拒，对位
    `validate_config_path` 的 UTF-8 检查点）。

    段内原生非 ASCII 字符先按 UTF-8 字节流处理（WHATWG 解析器同样会将其
    再编码为 UTF-8 百分号序列——直接对 UTF-8 字节流解码等价）。"""
    if "%" not in segment:
        return segment
    data = segment.encode("utf-8")
    out = bytearray()
    i = 0
    while i < len(data):
        # 仅解码严格的 %XX 十六进制对（对位 urlencoding 的逐字节语义——
        # 非十六进制字符不透视为数字，原样保留 `%`）
        if (
            data[i : i + 1] == b"%"
            and i + 3 <= len(data)
            and all(chr(c) in "0123456789abcdefABCDEF" for c in data[i + 1 : i + 3])
        ):
            out.append(int(data[i + 1 : i + 3].decode("ascii"), 16))
            i += 3
            continue
        out.append(data[i])
        i += 1
    try:
        return bytes(out).decode("utf-8", "strict")
    except UnicodeDecodeError as err:
        raise PermissionPathError(f"'{raw_uri}' 含非法 UTF-8 路径字节") from err


def _encode_segment(segment: str) -> str:
    """段 → URI 百分号编码（与 exec_server_wire._file_url 同一安全集，
    去掉 `/`——段内不应出现分隔符）"""
    safe = ":@-._~!$&'()*+,;="
    return "".join(
        ch if (ch.isascii() and (ch.isalnum() or ch in safe)) else f"%{b:02X}"
        for ch in segment
        for b in ch.encode("utf-8")
    )


def _contains_glob_metacharacter(path: str) -> bool:
    """对位 config_path.rs:170-173（`* ? [ ] { }`——比 glob 扫描多花括号）"""
    return any(ch in "*?[]{}" for ch in path)


@dataclass(frozen=True)
class PermissionPathUri:
    """`file:` URI 的跨平台词汇模型（对位 codex `PathUri`，lib.rs:67）。

    内部持有**已解码、已词法归一**的段序列（`.`/`..`/空段在构造时折叠，
    对位 `path_uri_from_segments` 与 `Url` 解析的 WHATWG 归一）；Windows
    驱动器字母大写归一（对位 `with_normalized_windows_drive_letter`）。
    """

    host: str | None  # UNC 授权（小写归一）；None = 本地路径
    segments: tuple[str, ...]  # 非空解码段（POSIX 根 = 空元组）
    has_separator_segment: bool = False  # 段解码后含分隔符（fail-closed 标记）

    # ── 构造 ────────────────────────────────────────────────

    @classmethod
    def _build(
        cls, host: str | None, raw_segments: list[str], is_windows_hint: bool | None
    ) -> "PermissionPathUri":
        """段序列归一（对位 absolute_path_normalization.rs `path_uri_from_segments`：
        折叠 `.`/`..`/空段；`..` 不越锚点——Windows 锚深 1、POSIX 锚深 0）"""
        anchor_depth = 1 if (is_windows_hint or host is not None) else 0
        depth = 0
        normalized: list[str] = []
        trailing_separator = False
        for segment in raw_segments:
            if segment == "":
                trailing_separator = True
            elif segment == ".":
                trailing_separator = False
            elif segment == "..":
                trailing_separator = False
                if depth > anchor_depth:
                    normalized.pop()
                    depth -= 1
            else:
                normalized.append(segment)
                depth += 1
                trailing_separator = False
        # 尾分隔符仅影响 URI 拼写（本侧模型不保留尾空段；Windows 驱动器根
        # 的渲染由 inferred_native_path_string 兜底 `C:\`）
        del trailing_separator
        if host is None and normalized and _is_windows_drive_segment(normalized[0]):
            normalized[0] = normalized[0][0].upper() + ":"
        has_separator_segment = any("/" in seg or "\\" in seg for seg in normalized)
        return cls(
            host=host,
            segments=tuple(normalized),
            has_separator_segment=has_separator_segment,
        )

    @classmethod
    def parse(cls, uri: str) -> "PermissionPathUri":
        """解析并校验 `file:` URI（对位 lib.rs:104-106 `parse` +
        `TryFrom<Url>` 校验族：凭证/端口/query/fragment/非法 UTF-8 皆拒）"""
        if "\x00" in uri:
            raise PermissionPathError(f"'{uri}' 不是合法的 file URI 路径")
        if not uri.startswith("file://"):
            scheme = uri.split(":", 1)[0] if ":" in uri else uri
            raise PermissionPathError(f"unsupported path URI scheme `{scheme}`")
        rest = uri[len("file://") :]
        for ch, message in (("?", "query parameters"), ("#", "fragments")):
            if ch in rest:
                raise PermissionPathError(f"{message} are not allowed in path URIs")
        slash = rest.find("/")
        authority = rest if slash == -1 else rest[:slash]
        path_part = "" if slash == -1 else rest[slash + 1 :]
        if "@" in authority:
            raise PermissionPathError("credentials are not allowed in path URIs")
        if ":" in authority:
            raise PermissionPathError("ports are not allowed in path URIs")
        # 对位 without_localhost_authority（lib.rs:709-716）+ WHATWG 主机小写
        host = authority.lower() or None
        if host == "localhost":
            host = None
        raw_segments = [_decode_segment(seg, uri) for seg in path_part.split("/")]
        is_windows_hint = host is not None or any(
            _is_windows_drive_segment(seg) for seg in raw_segments if seg
        )
        return cls._build(host, raw_segments, is_windows_hint)

    @classmethod
    def from_absolute_native_path(
        cls, path: str, convention: PermissionPathConvention
    ) -> "PermissionPathUri | None":
        """原生绝对路径 → URI（对位 lib.rs:148-163 `from_absolute_native_path`；
        无法普通拼写时返回 None——codex 落 opaque 兜底 URI，本侧裁剪为
        None，由调用侧在校验点报错）"""
        if "\x00" in path:
            return None
        if convention is PermissionPathConvention.POSIX:
            if not path.startswith("/"):
                return None
            return cls._build(None, path[1:].split("/"), False)
        # Windows：先归一命名空间别名（\\?\C:\… → C:\…；\\?\UNC\s\… → \\s\…）
        normalized = normalize_windows_device_path(path)
        if normalized is not None:
            path = normalized
        elif (
            len(path) >= 4
            and path[0] in "\\/"
            and path[1] in "\\/"
            and path[2] in ".?"
            and path[3] in "\\/"
        ):
            # 残余命名空间拼写（\\?\、\\.\\ 的非驱动器形态）——codex 落
            # opaque（lib.rs:876-879），本侧返回 None 由校验点拒绝
            return None
        # 驱动器绝对路径（C:\ 或 C:/ 开头）
        if (
            len(path) >= 3
            and path[0].isascii()
            and path[0].isalpha()
            and path[1] == ":"
            and path[2] in "\\/"
        ):
            segments = [path[:2], *_WINDOWS_SPLIT_RE.split(path[3:])]
            return cls._build(None, segments, True)
        # UNC（\\server\share\…）
        if len(path) >= 2 and path[0] in "\\/" and path[1] in "\\/":
            components = _WINDOWS_SPLIT_RE.split(path[2:])
            host = components[0] if components else ""
            share = components[1] if len(components) > 1 else ""
            if not host or not share or host in (".", "..") or share in (".", ".."):
                return None  # codex 落 opaque（lib.rs:898-912）——本侧裁剪
            return cls._build(host.lower(), components[1:], True)
        return None

    # ── 观察 ────────────────────────────────────────────────

    def infer_path_convention(self) -> PermissionPathConvention | None:
        """对位 lib.rs:235-253（授权 → Windows UNC；首段驱动器 → Windows；
        其余 → POSIX）"""
        if self.host is not None:
            return PermissionPathConvention.WINDOWS
        if self.segments and _is_windows_drive_segment(self.segments[0]):
            return PermissionPathConvention.WINDOWS
        return PermissionPathConvention.POSIX

    def inferred_native_path_string(self) -> str:
        """按推断约定渲染原生路径（对位 lib.rs:261-266 +
        api_path_string.rs `render_posix_path`/`render_windows_path`）"""
        convention = self.infer_path_convention()
        if convention is None:
            return self.to_uri_string()
        return self._render(convention)

    def _render(self, convention: PermissionPathConvention) -> str:
        """对位 api_path_string.rs:69-81 `from_path_uri`（约定不符即拒）"""
        if convention is PermissionPathConvention.POSIX:
            if self.host is not None:
                raise PermissionPathError(
                    f"path URI `{self.to_uri_string()}` cannot be rendered using "
                    f"POSIX path syntax"
                )
            return "/" + "/".join(self.segments)
        # Windows
        if self.host is not None:
            if not self.segments or not self.segments[0]:
                raise PermissionPathError(
                    f"path URI `{self.to_uri_string()}` cannot be rendered using "
                    f"Windows path syntax"
                )
            rendered = "\\\\" + self.host + "\\" + "\\".join(self.segments)
            return rendered
        if not self.segments or not _is_windows_drive_segment(self.segments[0]):
            raise PermissionPathError(
                f"path URI `{self.to_uri_string()}` cannot be rendered using "
                f"Windows path syntax"
            )
        rendered = self.segments[0]
        for segment in self.segments[1:]:
            rendered += "\\" + segment
        if len(rendered) == 2 and rendered[1] == ":":
            rendered += "\\"  # 驱动器根（对位 api_path_string.rs:378-380）
        return rendered

    def to_uri_string(self) -> str:
        """规范 URI 拼写（serde 线上形态对位：PathUri 序列化为 URI 字符串）"""
        authority = self.host or ""
        encoded = "/".join(_encode_segment(seg) for seg in self.segments)
        return f"file://{authority}/{encoded}" if encoded else f"file://{authority}/"

    def basename(self) -> str | None:
        """对位 lib.rs:273-282"""
        return self.segments[-1] if self.segments else None

    def parent(self) -> "PermissionPathUri | None":
        """词法父级（不越推断约定的根——对位 lib.rs:293-320）"""
        convention = self.infer_path_convention()
        if convention is None:
            return None
        anchor_depth = 1 if convention is PermissionPathConvention.WINDOWS else 0
        if len(self.segments) <= anchor_depth:
            return None
        return PermissionPathUri(
            host=self.host,
            segments=self.segments[:-1],
            has_separator_segment=False,
        )

    def starts_with(self, base: "PermissionPathUri") -> bool:
        """词法包含（对位 lib.rs:335-358：同授权同约定 + 段前缀；
        Windows 段 ASCII 大小写不敏感；编码分隔符段 fail-closed）"""
        if self == base:
            return True
        if self.has_separator_segment or base.has_separator_segment:
            return False
        if self.host != base.host:
            return False
        convention = self.infer_path_convention()
        if convention != base.infer_path_convention():
            return False
        if len(self.segments) < len(base.segments):
            return False
        for own, other in zip(self.segments, base.segments):
            if convention is PermissionPathConvention.WINDOWS:
                if own.lower() != other.lower():
                    return False
            elif own != other:
                return False
        return True

    def __eq__(self, other: object) -> bool:
        """对位 lib.rs:69-82 的 PartialEq（URL 相等优先，Windows 身份字节
        ASCII 大小写折叠兜底）"""
        if not isinstance(other, PermissionPathUri):
            return NotImplemented
        if self.host == other.host and self.segments == other.segments:
            return True
        if (
            self.infer_path_convention() is PermissionPathConvention.WINDOWS
            and other.infer_path_convention() is PermissionPathConvention.WINDOWS
            and not self.has_separator_segment
            and not other.has_separator_segment
        ):
            return self.host == other.host and tuple(
                seg.lower() for seg in self.segments
            ) == tuple(seg.lower() for seg in other.segments)
        return False

    def __hash__(self) -> int:
        if self.infer_path_convention() is PermissionPathConvention.WINDOWS:
            return hash((self.host, tuple(seg.lower() for seg in self.segments)))
        return hash((self.host, self.segments))

    def __str__(self) -> str:
        return self.to_uri_string()

    # ── 变换 ────────────────────────────────────────────────

    def join(self, path: str) -> "PermissionPathUri":
        """词法拼接（逐行对位 lib.rs:448-535 `join`）"""
        if "\x00" in path:
            raise PermissionPathError(f"'{path}' 不是合法的 file URI 路径")
        if path == "":
            return self
        convention = self.infer_path_convention()
        if convention is None:
            raise PermissionPathError(f"'{self}' 无路径约定")
        # 绝对路径整体替换基座（对位 lib.rs:462-465）
        absolute = PermissionPathUri.from_absolute_native_path(path, convention)
        if absolute is not None:
            return absolute
        if (
            convention is PermissionPathConvention.WINDOWS
            and len(path) >= 2
            and path[0].isascii()
            and path[0].isalpha()
            and path[1] == ":"
        ):
            # 驱动器相对路径：同驱动器拼入，跨驱动器拒绝（对位 lib.rs:468-487）
            same_drive = (
                bool(self.segments)
                and _is_windows_drive_segment(self.segments[0])
                and self.segments[0][0].lower() == path[0].lower()
            )
            if not same_drive:
                raise PermissionPathError(f"'{path}' 不是合法的 file URI 路径")
            path = path[2:]
        anchor_depth = 1 if convention is PermissionPathConvention.WINDOWS else 0
        segments = list(self.segments)
        windows_root_relative = (
            convention is PermissionPathConvention.WINDOWS
            and len(path) >= 1
            and path[0] in "\\/"
            and not (len(path) >= 2 and path[1] in "\\/")
        )
        if windows_root_relative:
            while len(segments) > anchor_depth:
                segments.pop()
        if convention is PermissionPathConvention.WINDOWS:
            path = path.replace("\\", "/")
        for component in path.split("/"):
            if component in ("", "."):
                continue
            if component == "..":
                if len(segments) > anchor_depth:
                    segments.pop()
            else:
                segments.append(component)
        return PermissionPathUri(
            host=self.host,
            segments=tuple(segments),
            has_separator_segment=False,
        )

    # ── 配置路径校验/解析（对位 config_path.rs） ─────────────

    def to_config_path_string(self, convention: PermissionPathConvention) -> str:
        """渲染为配置路径文本（对位 config_path.rs:64-73：UNC 共享根补
        尾分隔符）"""
        rendered = self._render(convention)
        if self.host is not None and self.parent() is None:
            rendered += "\\"
        return rendered

    def validate_config_path(self, convention: PermissionPathConvention) -> None:
        """已解析 URI 的有损拼写检查（对位 config_path.rs:94-118）"""
        if self.infer_path_convention() is not convention:
            raise PermissionPathError(
                f"path URI `{self.to_uri_string()}` cannot be rendered using "
                f"{convention} path syntax"
            )
        if self.has_separator_segment:
            raise PermissionPathError(
                f"'{self.to_uri_string()}' 不是合法的 file URI 路径"
            )
        rendered = self._render(convention)
        validate_config_path_text(rendered, convention)

    def validate_glob_directory(self, convention: PermissionPathConvention) -> None:
        """字面目录可原样嵌入 glob 的检查（对位 config_path.rs:77-90：
        元字符拒转义；POSIX 反斜杠拒）"""
        self.validate_config_path(convention)
        rendered = self._render(convention)
        if _contains_glob_metacharacter(rendered) or (
            convention is PermissionPathConvention.POSIX and "\\" in rendered
        ):
            raise PermissionPathError(
                f'unsupported configuration path "{rendered}" using '
                f"{convention} path syntax"
            )

    @staticmethod
    def resolve_config_path_uri(
        input: str,
        convention: PermissionPathConvention,
        base: "PermissionPathUri | None",
        user_home_dir: "PermissionPathUri | None",
    ) -> "PermissionPathUri":
        """配置文本 → URI（逐行对位 config_path.rs:26-60
        `resolve_config_path_uri`）"""
        validate_config_path_text(input, convention)
        home_suffix = convention.home_relative_suffix(input)
        if home_suffix is not None:
            if user_home_dir is None:
                raise PermissionPathError(
                    f"cannot resolve home-relative path `{input}` without an "
                    f"executor home"
                )
            user_home_dir.validate_config_path(convention)
            if _contains_glob_metacharacter(input):
                user_home_dir.validate_glob_directory(convention)
            resolved = resolve_against_uri(input, user_home_dir, user_home_dir)
        else:
            absolute = PermissionPathUri.from_absolute_native_path(input, convention)
            if absolute is not None:
                resolved = absolute
            else:
                if base is None:
                    raise PermissionPathError(
                        f"path `{input}` is not absolute using {convention} "
                        f"path syntax"
                    )
                base.validate_config_path(convention)
                if _contains_glob_metacharacter(input):
                    base.validate_glob_directory(convention)
                resolved = resolve_against_uri(input, base, None)
        resolved.validate_config_path(convention)
        # 配置路径省略尾分隔符（根部除外）——join(".") 在本模型中恒等
        return resolved.join(".")


def validate_config_path_text(input: str, convention: PermissionPathConvention) -> None:
    """原生配置文本校验（逐行对位 config_path.rs:122-167）"""
    namespace_alias = normalize_windows_device_path(input)
    native_input = namespace_alias if namespace_alias is not None else input
    has_windows_component_colon = False
    if convention is PermissionPathConvention.WINDOWS:
        for index, segment in enumerate(convention.path_segments(native_input)):
            if (
                index == 0
                and len(segment) >= 2
                and segment[0].isascii()
                and segment[0].isalpha()
                and segment[1] == ":"
            ):
                segment = segment[2:]
            if ":" in segment:
                has_windows_component_colon = True
                break
    mixed_home_separators = False
    if convention is PermissionPathConvention.WINDOWS:
        suffix = convention.home_relative_suffix(input)
        if suffix is not None:
            mixed_home_separators = (
                suffix.startswith("/") and suffix.lstrip("/").startswith("\\")
            ) or (suffix.startswith("\\") and suffix.lstrip("\\").startswith("/"))
    bare_windows_drive = convention is PermissionPathConvention.WINDOWS and (
        len(native_input) == 2
        and native_input[0].isascii()
        and native_input[0].isalpha()
        and native_input[1] == ":"
    )
    if (
        "\x00" in input
        or has_windows_component_colon
        or mixed_home_separators
        or bare_windows_drive
    ):
        raise PermissionPathError(
            f'unsupported configuration path "{input}" using {convention} '
            f"path syntax"
        )


def infer_absolute_path_convention(path: str) -> PermissionPathConvention | None:
    """从拼写推断绝对路径约定（对位 api_path_string.rs:170-189：
    驱动器根/双分隔符 → Windows；`/` 前缀 → POSIX；其余 None）"""
    has_windows_drive_root = (
        len(path) >= 3
        and path[0].isascii()
        and path[0].isalpha()
        and path[1] == ":"
        and path[2] in "\\/"
    )
    has_windows_unc_root = len(path) >= 2 and path[0] in "\\/" and path[1] in "\\/"
    if has_windows_drive_root or has_windows_unc_root:
        return PermissionPathConvention.WINDOWS
    if path.startswith("/"):
        return PermissionPathConvention.POSIX
    return None


def resolve_against_uri(
    path: str, cwd: PermissionPathUri, user_home_dir: PermissionPathUri | None
) -> PermissionPathUri:
    """原生拼写对 executor cwd 的解析（逐行对位 api_path_string.rs:102-140
    `LegacyAppPathString::resolve_against`）"""
    convention = cwd.infer_path_convention()
    if convention is None:
        raise PermissionPathError(f"path URI `{cwd}` has no path convention")
    is_windows = convention is PermissionPathConvention.WINDOWS
    home_suffix = convention.home_relative_suffix(path)
    if home_suffix is not None:
        if user_home_dir is None:
            raise PermissionPathError(
                f"cannot resolve home-relative path `{path}` without an "
                f"executor home"
            )
        trimmed = home_suffix.lstrip("/" + ("\\" if is_windows else ""))
        return user_home_dir.join(trimmed)
    if is_windows and (path.startswith("//") or path.startswith("\\\\")):
        resolved = PermissionPathUri.from_absolute_native_path(path, convention)
        if resolved is None:
            raise PermissionPathError(
                f"path `{path}` is not absolute using Windows path syntax"
            )
        return resolved
    inferred = infer_absolute_path_convention(path)
    if inferred is not None and inferred is convention:
        resolved = PermissionPathUri.from_absolute_native_path(path, convention)
        if resolved is None:
            raise PermissionPathError(
                f"path `{path}` is not absolute using {convention} path syntax"
            )
        return resolved
    if inferred is PermissionPathConvention.POSIX and is_windows:
        return cwd.join(path)
    if inferred is not None:
        raise PermissionPathError(
            f"path {path} uses {inferred} paths, but executor cwd {cwd} uses "
            f"{convention} paths"
        )
    return cwd.join(path)


@dataclass(frozen=True)
class PermissionPathContext:
    """配置解析的路径事实（对位 codex `ConfigPathContext`，
    config/src/path_context.rs:12-31——约定 + 基准目录 + home，纯值对象）。

    权限编译管线中 base_dir 即会话 cwd（对位 core/src/config/mod.rs:3515-3520
    以 resolved_cwd 为基准）。
    """

    convention: PermissionPathConvention
    base_dir: PermissionPathUri | None = None
    user_home_dir: PermissionPathUri | None = None

    @classmethod
    def of_uris(
        cls,
        convention: PermissionPathConvention,
        base_dir: str | None = None,
        user_home_dir: str | None = None,
    ) -> "PermissionPathContext":
        """以 `file://` URI 文本构造（金标测试对位 codex `PathUri::parse`）"""
        return cls(
            convention=convention,
            base_dir=PermissionPathUri.parse(base_dir) if base_dir else None,
            user_home_dir=(
                PermissionPathUri.parse(user_home_dir) if user_home_dir else None
            ),
        )

    @classmethod
    def of_native(
        cls,
        convention: PermissionPathConvention,
        base_dir: str | None = None,
        user_home_dir: str | None = None,
    ) -> "PermissionPathContext":
        """以原生绝对路径文本构造（loader 接线入口）"""

        def _convert(value: str | None) -> PermissionPathUri | None:
            if value is None:
                return None
            uri = PermissionPathUri.from_absolute_native_path(value, convention)
            if uri is None:
                raise PermissionPathError(
                    f"path `{value}` is not absolute using {convention} path syntax"
                )
            return uri

        return cls(
            convention=convention,
            base_dir=_convert(base_dir),
            user_home_dir=_convert(user_home_dir),
        )

    def resolve_path(self, input: str) -> PermissionPathUri:
        """对位 path_context.rs:39-46"""
        return PermissionPathUri.resolve_config_path_uri(
            input, self.convention, self.base_dir, self.user_home_dir
        )

    def resolve_against(self, input: str, base: PermissionPathUri) -> PermissionPathUri:
        """对位 path_context.rs:49-60"""
        return PermissionPathUri.resolve_config_path_uri(
            input, self.convention, base, self.user_home_dir
        )
