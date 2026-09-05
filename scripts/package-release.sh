#!/usr/bin/env bash
#
# 合包归档：前端平台目录 + 后端 runtime/ → 发布归档（tar.gz / zip）+ sha256 记录。
#
# 用法：
#   scripts/package-release.sh --frontend <dir> --backend <dir> --platform <name> [--out <dir>]
#
# - --frontend：build-frontend.sh 产出的平台目录（nova[.exe] + 随行资产）
# - --backend：build-backend.sh 产出的输出目录（其下 runtime/ 被整体并入）
# - --out：归档输出目录（缺省 dist/release；相对路径按仓根解析——
#   脚本锚定仓根，从任何 cwd 调用结果一致）
#
# 归档扁平布局（解压即用——TUI 的后端发现链认同目录 runtime/nova-server）：
#   nova[.exe]  runtime/  CHANGELOG.md  package.json  LICENSE  export/  native/
#
# 归档按平台后缀：windows-* → zip，其余 → tar.gz（tar 保可执行位）。

set -euo pipefail

# 锚定仓根：合包是仓级行为，入参与产物路径一律按仓根解析
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

FRONTEND=""
BACKEND=""
PLATFORM=""
OUT=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --frontend)
            FRONTEND="$2"
            shift 2
            ;;
        --backend)
            BACKEND="$2"
            shift 2
            ;;
        --platform)
            PLATFORM="$2"
            shift 2
            ;;
        --out)
            OUT="$2"
            shift 2
            ;;
        *)
            echo "未知选项: $1"
            exit 1
            ;;
    esac
done

if [[ -z "$FRONTEND" || -z "$BACKEND" || -z "$PLATFORM" ]]; then
    echo "用法: $0 --frontend <dir> --backend <dir> --platform <name> [--out <dir>]"
    exit 1
fi
if [[ ! -d "$FRONTEND" ]]; then
    echo "错误：前端目录不存在: $FRONTEND"
    exit 1
fi
if [[ ! -d "$BACKEND/runtime" ]]; then
    echo "错误：后端产物缺 runtime/ 目录: $BACKEND"
    exit 1
fi
case "$PLATFORM" in
    darwin-arm64|darwin-x64|linux-x64|linux-arm64|windows-x64|windows-arm64) ;;
    *)
        echo "非法平台: $PLATFORM"
        exit 1
        ;;
esac

if [[ -z "$OUT" ]]; then
    OUT="$REPO_ROOT/dist/release"
fi
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

STAGING="$(mktemp -d "${TMPDIR:-/tmp}/nova-package.XXXXXX")"
cleanup() {
    rm -rf "$STAGING"
}
trap cleanup EXIT

cp -R "$FRONTEND"/. "$STAGING"/
rm -rf "$STAGING/runtime"
cp -R "$BACKEND/runtime" "$STAGING/runtime"

# 基础完整性门：两个可执行入口必须在
if [[ "$PLATFORM" == windows-* ]]; then
    test -f "$STAGING/nova.exe" || { echo "错误：缺 nova.exe"; exit 1; }
    test -f "$STAGING/runtime/nova-server.exe" || { echo "错误：缺 runtime/nova-server.exe"; exit 1; }
    ARCHIVE="$OUT/nova-${PLATFORM}.zip"
else
    test -f "$STAGING/nova" || { echo "错误：缺 nova"; exit 1; }
    test -f "$STAGING/runtime/nova-server" || { echo "错误：缺 runtime/nova-server"; exit 1; }
    ARCHIVE="$OUT/nova-${PLATFORM}.tar.gz"
fi

rm -f "$ARCHIVE"
if [[ "$ARCHIVE" == *.zip ]]; then
    if command -v zip >/dev/null 2>&1; then
        (cd "$STAGING" && zip -qr "$ARCHIVE" .)
    else
        # Windows runner 的 Git Bash 无 zip——回退 PowerShell（路径转 Windows 形态）
        SRC="$(cygpath -w "$STAGING")"
        DST="$(cygpath -w "$ARCHIVE")"
        powershell.exe -NoProfile -NonInteractive -Command \
            "Compress-Archive -Path \"${SRC}\\*\" -DestinationPath \"${DST}\" -Force"
    fi
else
    (cd "$STAGING" && tar -czf "$ARCHIVE" .)
fi

# sha256 记录（追加——多平台连调时逐条累积成 SHA256SUMS）
if command -v sha256sum >/dev/null 2>&1; then
    (cd "$OUT" && sha256sum "$(basename "$ARCHIVE")" >> SHA256SUMS)
else
    (cd "$OUT" && shasum -a 256 "$(basename "$ARCHIVE")" >> SHA256SUMS)
fi

echo "==> 归档完成: $ARCHIVE"
ls -lh "$ARCHIVE"
