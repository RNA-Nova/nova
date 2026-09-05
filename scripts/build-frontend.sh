#!/usr/bin/env bash
#
# 前端二进制构建（bun --compile 六目标交叉编译——bun 可交叉，单 runner 出全部）。
#
# 用法：
#   scripts/build-frontend.sh [--skip-install] [--skip-build] [--platform <name>] [--out <dir>]
#
# 选项：
#   --skip-install    跳过 npm ci
#   --skip-build      跳过 tsc 构建（dist/ 已是最新时用）
#   --platform <name> 只建指定平台（darwin-arm64 / darwin-x64 / linux-x64 /
#                     linux-arm64 / windows-x64 / windows-arm64）
#   --out <dir>       输出目录（缺省 dist/frontend；相对路径一律按仓根解析——
#                     脚本锚定仓根，从任何 cwd 调用结果一致）
#
# 产物布局（每平台一个目录；runtime/ 后端由 build-backend.sh 产出，
# 合包归档归 scripts/package-release.sh）：
#   <out>/<platform>/nova[.exe]      # 编译产物（--define 注入 __NOVA_VERSION__）
#   <out>/<platform>/package.json    # 版本戳兜底 + 元数据
#   <out>/<platform>/CHANGELOG.md    # /changelog 与 What's New 数据源
#   <out>/<platform>/LICENSE         # 分发合规
#   <out>/<platform>/export/         # /export HTML 模板资产（copy-assets 产物）
#   <out>/<platform>/native/         # 终端输入原生助手（darwin/windows 预编译件）

set -euo pipefail

# 锚定仓根：发布是仓级行为，一切相对路径（含 --out）按仓根解析
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
PKG_ROOT="$REPO_ROOT/packages/nova-tui"

SKIP_INSTALL=false
SKIP_BUILD=false
PLATFORM=""
OUTPUT_DIR=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-install)
            SKIP_INSTALL=true
            shift
            ;;
        --skip-build)
            SKIP_BUILD=true
            shift
            ;;
        --platform)
            PLATFORM="$2"
            shift 2
            ;;
        --out)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        *)
            echo "未知选项: $1"
            exit 1
            ;;
    esac
done

ALL_PLATFORMS=(darwin-arm64 darwin-x64 linux-x64 linux-arm64 windows-x64 windows-arm64)

if [[ -n "$PLATFORM" ]]; then
    case " ${ALL_PLATFORMS[*]} " in
        *" $PLATFORM "*) ;;
        *)
            echo "非法平台: $PLATFORM（合法值：${ALL_PLATFORMS[*]}）"
            exit 1
            ;;
    esac
    PLATFORMS=("$PLATFORM")
else
    PLATFORMS=("${ALL_PLATFORMS[@]}")
fi

if [[ -z "$OUTPUT_DIR" ]]; then
    OUTPUT_DIR="$REPO_ROOT/dist/frontend"
fi
if [[ "$OUTPUT_DIR" != /* ]]; then
    OUTPUT_DIR="$REPO_ROOT/$OUTPUT_DIR"
fi

# bun 目标名映射：x64 一律 baseline 后缀（老 CPU 兼容面），arm64 无后缀
bun_target_for() {
    case "$1" in
        darwin-arm64)   echo "bun-darwin-arm64" ;;
        darwin-x64)     echo "bun-darwin-x64-baseline" ;;
        linux-x64)      echo "bun-linux-x64-baseline" ;;
        linux-arm64)    echo "bun-linux-arm64" ;;
        windows-x64)    echo "bun-windows-x64-baseline" ;;
        windows-arm64)  echo "bun-windows-arm64" ;;
    esac
}

BUN="${BUN:-bun}"
if ! command -v "$BUN" >/dev/null 2>&1; then
    echo "错误：找不到 bun（BUN 环境变量可指定路径）"
    exit 1
fi

if [[ "$SKIP_INSTALL" == "false" ]]; then
    echo "==> npm ci"
    (cd "$PKG_ROOT" && npm ci)
fi

if [[ "$SKIP_BUILD" == "false" ]]; then
    echo "==> tsc 构建 + 资产拷贝（npm run build）"
    (cd "$PKG_ROOT" && npm run build)
fi

# 版本戳：单一事实源是 package.json——编译态经 --define 注入，
# 同文件也拷到二进制旁作运行期兜底
VERSION="$(node -p "require('$PKG_ROOT/package.json').version")"
echo "==> 版本戳: $VERSION"

# 全量构建清整个输出目录；单平台构建只清该平台目录（不毁兄弟平台产物）
if [[ -n "$PLATFORM" ]]; then
    rm -rf "$OUTPUT_DIR/$PLATFORM"
else
    rm -rf "$OUTPUT_DIR"
fi
for platform in "${PLATFORMS[@]}"; do
    mkdir -p "$OUTPUT_DIR/$platform"
done

for platform in "${PLATFORMS[@]}"; do
    bun_target="$(bun_target_for "$platform")"
    if [[ "$platform" == windows-* ]]; then
        outfile="$OUTPUT_DIR/$platform/nova.exe"
    else
        outfile="$OUTPUT_DIR/$platform/nova"
    fi
    echo "==> 编译 ${platform}（target=${bun_target}）"
    # --no-compile-autoload-bunfig：防用户 cwd 的 bunfig.toml preload
    # 在二进制启动前崩掉宿主（上游教训）
    "$BUN" build --compile --no-compile-autoload-bunfig \
        --target="$bun_target" \
        --define "__NOVA_VERSION__:\"$VERSION\"" \
        "$PKG_ROOT/dist/modes/tui/main.js" \
        --outfile "$outfile"
done

echo "==> 随行资产拷贝"
PI_TUI_NATIVE="$PKG_ROOT/node_modules/@earendil-works/pi-tui/native"
for platform in "${PLATFORMS[@]}"; do
    dir="$OUTPUT_DIR/$platform"
    cp "$PKG_ROOT/package.json" "$dir/"
    cp "$REPO_ROOT/CHANGELOG.md" "$dir/"
    cp "$REPO_ROOT/LICENSE" "$dir/"
    # /export HTML 模板（copy-assets.mjs 拷到 dist/export/ 的运行时资产——
    # 只拷模板与 vendor，不带 tsc 产物）
    mkdir -p "$dir/export/vendor"
    cp "$PKG_ROOT/dist/export/template.html" "$PKG_ROOT/dist/export/template.css" "$PKG_ROOT/dist/export/template.js" "$dir/export/"
    cp "$PKG_ROOT/dist/export/vendor/marked.min.js" "$PKG_ROOT/dist/export/vendor/highlight.min.js" "$dir/export/vendor/"

    # 终端输入原生助手：运行期按 dirname(process.execPath)/native/... 解析
    # （pi-tui 的 native/ 包内携带全平台预编译件，无需跨平台依赖安装）
    if [[ "$platform" == darwin-* ]]; then
        mkdir -p "$dir/native/darwin/prebuilds/$platform"
        cp "$PI_TUI_NATIVE/darwin/prebuilds/$platform/darwin-modifiers.node" \
            "$dir/native/darwin/prebuilds/$platform/"
    fi
    if [[ "$platform" == windows-* ]]; then
        if [[ "$platform" == "windows-arm64" ]]; then
            win32_arch_dir="win32-arm64"
        else
            win32_arch_dir="win32-x64"
        fi
        mkdir -p "$dir/native/win32/prebuilds/$win32_arch_dir"
        cp "$PI_TUI_NATIVE/win32/prebuilds/$win32_arch_dir/win32-console-mode.node" \
            "$dir/native/win32/prebuilds/$win32_arch_dir/"
    fi
done

echo ""
echo "==> 前端构建完成（${VERSION}）"
ls -lh "$OUTPUT_DIR"/*/nova* 2>/dev/null || true
