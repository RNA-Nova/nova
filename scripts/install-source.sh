#!/bin/sh
# nova 源码安装器——服务不装预编译二进制、直接从源码构建的用户。
#
# 用法：
#   curl -fsSL https://github.com/RNA-Nova/nova/releases/latest/download/install-source.sh | sh
#   sh scripts/install-source.sh            # 仓库内直接跑（用当前仓库，不 clone）
#   sh install-source.sh uninstall          # 卸载（摘 launcher + 删 venv，源码目录保留）
#
# 做的事：
#   1. 预检 git / python3（>=3.12）/ node（>=22.19）/ npm
#   2. 源码落位（缺省 clone 最新 release tag 到 ~/.nova/agent/src/nova；
#      在仓库内运行或设 NOVA_SOURCE_DIR 时直接用现有目录）
#   3. python3 -m venv <安装根>/venv + pip 安装四包（nova_ai / nova_agent /
#      nova_harness / nova_base——非 editable 静态副本）
#   4. 注册官方双 bundle（nova-pkg install path:，源码目录 editable 引用——
#      git pull 后 /reload 即更新）
#   5. nova-tui 前端构建（npm ci + npm run build）
#   6. 写 launcher：<bin>/nova（注入 NOVA_PYTHON=venv 后 exec node 入口）
#   7. 自检双端版本 + PATH 指引
#
# 环境变量：NOVA_SOURCE_DIR（现有源码树）/ NOVA_SOURCE_REF（clone 的 ref，
# 缺省最新 release tag）/ NOVA_INSTALL_DIR（缺省 ~/.nova/agent/install）/
# NOVA_BIN_DIR（缺省 ~/.local/bin）

set -eu

NOVA_REPO_URL="${NOVA_REPO_URL:-https://github.com/RNA-Nova/nova.git}"

say() { printf '%s\n' "$@"; }
err() { printf 'error: %s\n' "$@" >&2; }

install_root() { printf '%s' "${NOVA_INSTALL_DIR:-$HOME/.nova/agent/install}"; }
bin_dir() { printf '%s' "${NOVA_BIN_DIR:-$HOME/.local/bin}"; }

# —— 预检 ————————————————————————————————————————————————————————————

version_at_least() {  # version_at_least <实际> <最低>（点分十进制逐段比）
  actual="$1"; floor="$2"
  old_ifs=$IFS; IFS=.
  set -- $actual; a_major=$1; a_minor=${2:-0}; a_patch=${3:-0}
  set -- $floor; f_major=$1; f_minor=${2:-0}; f_patch=${3:-0}
  IFS=${old_ifs}
  [ "$a_major" -gt "$f_major" ] && return 0
  [ "$a_major" -lt "$f_major" ] && return 1
  [ "$a_minor" -gt "$f_minor" ] && return 0
  [ "$a_minor" -lt "$f_minor" ] && return 1
  [ "$a_patch" -ge "$f_patch" ]
}

run_preflight() {
  status=0
  command -v git >/dev/null 2>&1 || { err "需要 git"; status=1; }

  if command -v python3 >/dev/null 2>&1; then
    py_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')
    if version_at_least "$py_version" "3.12.0" && ! version_at_least "$py_version" "3.14.0"; then
      say "python3: $py_version"
    else
      err "Python 需要 >=3.12,<3.14（当前 ${py_version}）"
      status=1
    fi
  else
    err "需要 python3（>=3.12,<3.14）"
    status=1
  fi

  if command -v node >/dev/null 2>&1; then
    node_version=$(node --version)
    if version_at_least "${node_version#v}" "22.19.0"; then
      say "node: $node_version"
    else
      err "Node.js 需要 >=22.19.0（当前 ${node_version}）"
      status=1
    fi
  else
    err "需要 Node.js >=22.19.0"
    status=1
  fi
  command -v npm >/dev/null 2>&1 || { err "需要 npm"; status=1; }
  return "$status"
}

# —— 源码落位 —————————————————————————————————————————————————————————

resolve_source_dir() {
  if [ -n "${NOVA_SOURCE_DIR:-}" ]; then
    src="$NOVA_SOURCE_DIR"
  elif [ -f "$(dirname "$0")/../pyproject.toml" ] && [ -d "$(dirname "$0")/../packages/nova-tui" ]; then
    # 在仓库内直接运行——用当前仓库（脚本锚定仓根）
    src=$(cd "$(dirname "$0")/.." && pwd)
  else
    src="$HOME/.nova/agent/src/nova"
  fi
  printf '%s' "$src"
}

ensure_source() {
  src="$1"
  if [ -f "$src/packages/nova-tui/package.json" ]; then
    say "源码: ${src}（现有目录）"
    return 0
  fi
  ref="${NOVA_SOURCE_REF:-}"
  if [ -z "$ref" ]; then
    ref=$(git ls-remote --tags "$NOVA_REPO_URL" 2>/dev/null | sed -n 's|.*/v\([0-9][0-9.]*\)$|v\1|p' | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1)
    [ -n "$ref" ] || { err "无法解析最新 release tag（可设 NOVA_SOURCE_REF 显式指定）"; return 1; }
  fi
  say "clone: $NOVA_REPO_URL @ $ref → $src"
  mkdir -p "$(dirname "$src")"
  git clone --depth 1 --branch "$ref" "$NOVA_REPO_URL" "$src"
}

# —— 后端：venv + pip ————————————————————————————————————————————————

setup_backend() {
  src="$1"
  venv="$(install_root)/venv"
  say "venv: $venv"
  python3 -m venv "$venv"
  venv_python="$venv/bin/python"
  "$venv_python" -m pip install --quiet --upgrade pip
  say "pip install 四包（非 editable 静态副本）"
  "$venv_python" -m pip install --quiet \
    "$src/packages/nova_ai" \
    "$src/packages/nova_agent" \
    "$src/packages/nova_harness" \
    "$src/bundles/nova_base"

  # 注册官方双 bundle（editable path 源：引用源码目录，git pull 后 /reload 生效）
  say "注册官方 bundle（nova-base + nova-coding-agent）"
  "$venv/bin/nova-pkg" install --editable "path:$src/bundles/nova_base"
  "$venv/bin/nova-pkg" install --editable "path:$src/bundles/nova_coding_agent"
}

# —— 前端：npm 构建 + launcher ————————————————————————————————————————

setup_frontend() {
  src="$1"
  say "npm ci + build（packages/nova-tui）"
  (cd "$src/packages/nova-tui" && npm ci --no-fund --no-audit && npm run build)

  bin="$(bin_dir)"
  mkdir -p "$bin"
  launcher="$bin/nova"
  cat > "$launcher" <<EOF
#!/bin/sh
# nova 源码安装 launcher（install-source.sh 生成）：注入后端解释器后进 TUI
export NOVA_PYTHON="$(install_root)/venv/bin/python"
exec node "$src/packages/nova-tui/dist/modes/tui/main.js" "\$@"
EOF
  chmod 755 "$launcher"
  say "launcher: $launcher"
}

# —— PATH 指引（与 install.sh 同语义） ————————————————————————————————

bin_dir_on_path() {
  case ":${PATH}:" in
    *":$(bin_dir):"*) return 0 ;;
    *) return 1 ;;
  esac
}

print_path_guidance() {
  bin_dir_on_path && return 0
  if [ "$(bin_dir)" = "$HOME/.local/bin" ]; then expr='$HOME/.local/bin'; else expr="$(bin_dir)"; fi
  say ""
  say "nova 已装到 $(bin_dir)/nova，但该目录不在 PATH。执行："
  say ""
  say "  export PATH=\"$expr:\$PATH\""
  say ""
}

# —— 卸载 ————————————————————————————————————————————————————————————

do_uninstall() {
  launcher="$(bin_dir)/nova"
  if [ -f "$launcher" ] && grep -q "install-source.sh" "$launcher" 2>/dev/null; then
    rm -f "$launcher"
    say "已摘 $launcher"
  else
    say "跳过 ${launcher}（非本安装器生成或不存在）"
  fi
  if [ -d "$(install_root)/venv" ]; then
    rm -rf "$(install_root)/venv"
    say "已删 $(install_root)/venv"
  fi
  say ""
  say "源码目录与用户数据保留（~/.nova/agent/src、~/.nova/agent 其余）。"
  say "如需彻底清除：rm -rf ~/.nova/agent"
}

# —— 主流程 ———————————————————————————————————————————————————————————

main() {
  if [ "${1:-}" = "uninstall" ]; then
    do_uninstall
    exit 0
  fi

  say ""
  printf '\033[1m  Nova 源码安装\033[0m\n'
  printf '\033[2m  从源码构建（不装预编译二进制）\033[0m\n\n'

  run_preflight

  src=$(resolve_source_dir)
  ensure_source "$src"

  setup_backend "$src"
  setup_frontend "$src"

  # 自检：后端 --version（venv）+ 前端 --version（launcher）
  backend_version="$("$(install_root)/venv/bin/python" -m nova_harness.cli.backend --version)"
  say "自检: 后端 $backend_version"
  tui_version="$("$(bin_dir)/nova" --version)"
  say "自检: 前端 $tui_version"

  print_path_guidance

  say ""
  say "安装完成。运行 nova 启动（后端经 NOVA_PYTHON 指向安装 venv）。"
  say "升级：cd $src && git pull，然后 /reload（包为 editable 引用）"
  say ""
}

main "$@"
