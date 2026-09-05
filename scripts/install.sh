#!/bin/sh
# nova 安装器（静态双二进制，零运行时依赖——不装 Node/Python）。
#
# 用法：
#   curl -fsSL https://github.com/RNA-Nova/nova/releases/latest/download/install.sh | sh
#   curl -fsSL ... | sh -s -- uninstall      # 卸载
#
# 安装矩阵：darwin-arm64 / darwin-x64 / linux-x64 / linux-arm64
# （windows 产物为 zip 手动安装；本脚本为 POSIX，Git Bash 支持留作后续）。
#
# 布局（版本并存 + 符号链接翻转，升级/回滚即翻链）：
#   <install根>/releases/<版本>/   # tarball 解压实体（nova + runtime/nova-server + 资产）
#   <install根>/current -> releases/<版本>
#   <bin目录>/nova -> <install根>/current/nova
#
# 环境变量（均有缺省，一般不需要）：
#   NOVA_VERSION                  钉版本（如 v0.1.0 或 0.1.0；缺省查 latest release）
#   NOVA_INSTALLER_RELEASES_BASE 发布源覆盖（缺省 GitHub releases；支持 file:// 本地演练）
#   NOVA_INSTALL_DIR              安装根（缺省 ~/.nova/agent/install）
#   NOVA_BIN_DIR                  bin 目录（缺省 ~/.local/bin）

set -eu

NOVA_REPO="RNA-Nova/nova"
NOVA_RELEASES_BASE="${NOVA_INSTALLER_RELEASES_BASE:-https://github.com/RNA-Nova/nova/releases}"

readonly NOVA_REPO NOVA_RELEASES_BASE

say() { printf '%s\n' "$@"; }
err() { printf 'error: %s\n' "$@" >&2; }

# —— 预检 ————————————————————————————————————————————————————————————

detect_platform() {
  os=$(uname -s)
  arch=$(uname -m)
  case "$os" in
    Darwin) nova_os=darwin ;;
    Linux) nova_os=linux ;;
    *)
      err "不支持的操作系统: ${os}（Windows 请手动下载 nova-windows-*.zip 解压使用）"
      return 1
      ;;
  esac
  case "$arch" in
    arm64|aarch64) nova_arch=arm64 ;;
    x86_64|amd64) nova_arch=x64 ;;
    *)
      err "不支持的 CPU 架构: $arch"
      return 1
      ;;
  esac
  printf '%s-%s' "$nova_os" "$nova_arch"
}

run_preflight() {
  command -v curl >/dev/null 2>&1 || { err "需要 curl"; return 1; }
  command -v tar >/dev/null 2>&1 || { err "需要 tar"; return 1; }
  if ! command -v sha256sum >/dev/null 2>&1 && ! command -v shasum >/dev/null 2>&1; then
    err "需要 sha256sum 或 shasum（校验下载完整性）"
    return 1
  fi
  platform=$(detect_platform)
  [ -n "$platform" ] || return 1
  say "平台: $platform"
  return 0
}

# —— 版本解析与下载 ———————————————————————————————————————————————————

resolve_version() {
  if [ -n "${NOVA_VERSION:-}" ]; then
    case "$NOVA_VERSION" in
      v*) printf '%s' "$NOVA_VERSION" ;;
      *) printf 'v%s' "$NOVA_VERSION" ;;
    esac
    return 0
  fi
  # latest release 的 tag（API 响应里取 tag_name 字段——installer 惯例的
  # 轻量解析，不依赖 jq）
  api="${NOVA_RELEASES_API_BASE:-https://api.github.com/repos/$NOVA_REPO/releases}"
  tag=$(curl -fsSL "$api/latest" | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n 1)
  if [ -z "$tag" ]; then
    err "无法解析最新发布版本（可设 NOVA_VERSION 显式指定）"
    return 1
  fi
  printf '%s' "$tag"
}

# 下载 <url> 到 <dest>（file:// 源用于本地演练，同样走 curl）
fetch() {
  url="$1"; dest="$2"
  if ! curl -fSL "$url" -o "$dest"; then
    rm -f "$dest"
    err "下载失败: $url"
    return 1
  fi
}

verify_sha256() {
  file="$1"; sums="$2"
  name=${file##*/}
  # 同名多行取最后一笔（SHA256SUMS 是追加语义——重跑同平台的最新条目在尾部）
  grep "  $name\$" "$sums" | tail -n 1 > "$sums.selected" || {
    err "SHA256SUMS 中没有 $name 的记录"
    return 1
  }
  dir=${file%/*}
  if command -v sha256sum >/dev/null 2>&1; then
    (cd "$dir" && sha256sum -c "$sums.selected" >/dev/null)
  else
    (cd "$dir" && shasum -a 256 -c "$sums.selected" >/dev/null)
  fi || {
    err "$name 的 sha256 校验失败——下载损坏或被篡改，未安装"
    return 1
  }
  say "校验: $name sha256 通过"
}

# —— 装配 ————————————————————————————————————————————————————————————

install_root() { printf '%s' "${NOVA_INSTALL_DIR:-$HOME/.nova/agent/install}"; }
bin_dir() { printf '%s' "${NOVA_BIN_DIR:-$HOME/.local/bin}"; }

# 已装版本（current-version 文件不在/读不出视为未装）
installed_version() {
  current_link="$(install_root)/current"
  [ -L "$current_link" ] || return 1
  target=$(readlink "$current_link") || return 1
  case "$target" in
    releases/*) printf '%s' "${target#releases/}" ;;
    *) return 1 ;;
  esac
}

activate_release() {
  version="$1"
  root="$(install_root)"
  ln -sfn "releases/$version" "$root/current"
  mkdir -p "$(bin_dir)"
  ln -sfn "$root/current/nova" "$(bin_dir)/nova"
}

# —— PATH —————————————————————————————————————————————————————————————

bin_dir_on_path() {
  case ":${PATH}:" in
    *":$(bin_dir):"*) return 0 ;;
    *) return 1 ;;
  esac
}

shell_profile_for() {
  current_shell=$(basename "${SHELL:-sh}")
  case "$current_shell" in
    fish) printf '%s/.config/fish/config.fish' "$HOME" ;;
    zsh) printf '%s/.zshrc' "${ZDOTDIR:-$HOME}" ;;
    bash)
      if [ -f "$HOME/.bashrc" ]; then printf '%s/.bashrc' "$HOME"; else printf '%s/.profile' "$HOME"; fi
      ;;
    *) printf '%s/.profile' "$HOME" ;;
  esac
}

path_snippet_for() {
  current_shell=$(basename "${SHELL:-sh}")
  if [ "$(bin_dir)" = "$HOME/.local/bin" ]; then expr='$HOME/.local/bin'; else expr="$(bin_dir)"; fi
  case "$current_shell" in
    fish) printf 'fish_add_path "%s"' "$expr" ;;
    *) printf 'export PATH="%s:$PATH"' "$expr" ;;
  esac
}

# tty 可用时交互写入 shell profile；否则打印指引（管道安装无 tty 也能完成）
maybe_setup_path() {
  bin_dir_on_path && return 0
  profile=$(shell_profile_for)
  snippet=$(path_snippet_for)
  if ( : <> /dev/tty ) 2>/dev/null; then
    printf '把 %s 加入 PATH（写入 %s）？[Y/n] ' "$(bin_dir)" "$profile" > /dev/tty
    if IFS= read -r answer < /dev/tty; then :; else answer=; fi
    case "$answer" in
      n|N|no|NO) ;;
      *)
        if ! grep -Fxq "$snippet" "$profile" 2>/dev/null; then
          mkdir -p "${profile%/*}"
          printf '\n# Nova\n%s\n' "$snippet" >> "$profile"
          say "已写入 ${profile}（重开 shell 或 source 后生效）"
        else
          say "$profile 已有该 PATH 配置"
        fi
        return 0
        ;;
    esac
  fi
  say ""
  say "nova 已装到 $(bin_dir)/nova，但该目录不在 PATH。执行："
  say ""
  say "  $snippet"
  say ""
}

# —— 卸载 —————————————————————————————————————————————————————————————

do_uninstall() {
  root="$(install_root)"
  link="$(bin_dir)/nova"
  removed=0
  # 安全闸：只摘指向本安装根的链接（不动别人放的同名 nova）
  if [ -L "$link" ]; then
    case "$(readlink "$link")" in
      "$root"/*) rm -f "$link"; say "已摘 $link"; removed=1 ;;
      *) say "跳过 ${link}（不指向本安装根，非本安装器所装）" ;;
    esac
  fi
  if [ -d "$root" ]; then
    rm -rf "$root"
    say "已删 $root"
    removed=1
  fi
  if [ "$removed" -eq 0 ]; then
    say "未发现本安装器的安装痕迹（${root}）"
  fi
  say ""
  say "用户数据保留在 ~/.nova/agent（settings/sessions/packages 等）。"
  say "如需彻底清除：rm -rf ~/.nova/agent"
}

# —— 主流程 ———————————————————————————————————————————————————————————

main() {
  if [ "${1:-}" = "uninstall" ]; then
    do_uninstall
    exit 0
  fi

  say ""
  printf '\033[1m  Nova Installer\033[0m\n'
  printf '\033[2m  框架 + TUI + 官方 bundle 的静态双二进制分发\033[0m\n\n'

  run_preflight

  version=$(resolve_version)
  root="$(install_root)"
  release_dir="$root/releases/$version"
  platform="$(detect_platform)"
  asset="nova-$platform.tar.gz"

  # 幂等：同版本已在位 → 只翻链（用户误重跑零代价）
  if [ -x "$release_dir/nova" ] && [ -x "$release_dir/runtime/nova-server" ]; then
    say "已存在 ${version}（${release_dir}）——跳过重装，直接激活"
  else
    stage="$root/staging/$version.$$"
    rm -rf "$stage"
    mkdir -p "$stage"
    trap 'rm -rf "$stage"' EXIT

    say "下载: $NOVA_RELEASES_BASE/download/$version/$asset"
    fetch "$NOVA_RELEASES_BASE/download/$version/$asset" "$stage/$asset"
    fetch "$NOVA_RELEASES_BASE/download/$version/SHA256SUMS" "$stage/SHA256SUMS"
    verify_sha256 "$stage/$asset" "$stage/SHA256SUMS"

    mkdir -p "$release_dir"
    tar -xzf "$stage/$asset" -C "$release_dir"
    rm -rf "$stage"
    rmdir "$root/staging" 2>/dev/null || true  # 摘空 staging 父目录，不留空壳
    trap - EXIT
    say "解压: $release_dir"
  fi

  activate_release "$version"

  # 装后自检：--version 报号必须与目标版本一致（抓到残缺/错版包）
  reported="$("$(bin_dir)/nova" --version 2>/dev/null || true)"
  expected="${version#v}"
  if [ "$reported" != "$expected" ]; then
    err "自检失败：nova --version 报 '$reported'，期望 '$expected'"
    exit 1
  fi
  say "自检: nova --version = $reported"

  maybe_setup_path

  say ""
  say "安装完成。运行 nova 启动（后端 nova-server 随二进制同行，无需额外安装）。"
  if [ -t 1 ] && bin_dir_on_path; then
    say "卸载：curl -fsSL $NOVA_RELEASES_BASE/latest/download/install.sh | sh -s -- uninstall"
  fi
  say ""
}

main "$@"
