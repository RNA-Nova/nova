#!/usr/bin/env bash
#
# 后端冻结构建（PyInstaller onedir——不能交叉编译，须按目标平台各建一份）。
#
# 用法：
#   scripts/build-backend.sh [--python <解释器>] [--out <dir>]
#
# 选项：
#   --python <path>   建 venv 用的解释器（缺省仓库 pixi dev 环境 python；
#                     亦可用环境变量 NOVA_BUILD_PYTHON 指定）
#   --out <dir>       输出目录（缺省 dist/backend；相对路径一律按仓根解析——
#                     脚本锚定仓根，从任何 cwd 调用结果一致）——产物整理为
#                     <out>/runtime/（内含 nova-server[.exe] 与 _internal/），
#                     供合包脚本并入前端平台目录
#
# 流程：干净 venv → pip install 四包（nova_ai / nova_agent / nova_harness /
# nova-base，非 editable——冻结要的是静态副本）+ pyinstaller → onedir 冻结
# nova_harness.cli.backend:main（--collect-all 四包 + --add-data 把
# bundles/nova_base 整条打进 _internal/bundles/nova_base，运行期
# ensure_builtin_packages 从 sys._MEIPASS 取它做首启落地）→ 版本戳自检。

set -euo pipefail

# 锚定仓根：输入跨四个包（发布是仓级行为），一切相对路径按仓根解析
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

PYTHON="${NOVA_BUILD_PYTHON:-$REPO_ROOT/.pixi/envs/dev/bin/python}"
OUTPUT_DIR=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --python)
            PYTHON="$2"
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

if [[ -z "$OUTPUT_DIR" ]]; then
    OUTPUT_DIR="$REPO_ROOT/dist/backend"
fi
if [[ "$OUTPUT_DIR" != /* ]]; then
    OUTPUT_DIR="$REPO_ROOT/$OUTPUT_DIR"
fi

if [[ ! -x "$PYTHON" ]]; then
    echo "错误：找不到 Python 解释器 ${PYTHON}（--python 或 NOVA_BUILD_PYTHON 指定）"
    exit 1
fi

# PyInstaller 版本钉住（验证过的构建版本；升级需回归本地冒烟）
PYINSTALLER_VERSION="6.22.2"

# Windows 的 --add-data 分隔符是分号（os.pathsep 语义）；且 venv 里是原生
# Windows Python——传给它的路径须是 Windows 形态（MSYS 只对"整个参数是路径"
# 做自动转换，--add-data 的复合参数不会转，显式 cygpath 最稳）
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
        ADD_DATA_SEP=';'
        nat() { cygpath -w "$1"; }
        ;;
    *)
        ADD_DATA_SEP=':'
        nat() { printf '%s' "$1"; }
        ;;
esac

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/nova-backend-build.XXXXXX")"
cleanup() {
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT

VENV="$WORK_DIR/venv"
echo "==> 建干净 venv（${PYTHON} → ${VENV}）"
"$PYTHON" -m venv "$VENV"
if [[ -x "$VENV/Scripts/python.exe" ]]; then
    VENV_PYTHON="$VENV/Scripts/python.exe"   # Windows
else
    VENV_PYTHON="$VENV/bin/python"
fi

echo "==> pip install 四包 + pyinstaller==$PYINSTALLER_VERSION"
"$VENV_PYTHON" -m pip install --quiet --upgrade pip
"$VENV_PYTHON" -m pip install --quiet \
    "$(nat "$REPO_ROOT/packages/nova_ai")" \
    "$(nat "$REPO_ROOT/packages/nova_agent")" \
    "$(nat "$REPO_ROOT/packages/nova_harness")" \
    "$(nat "$REPO_ROOT/bundles/nova_base")" \
    "pyinstaller==$PYINSTALLER_VERSION"

# 版本戳：构建期读出，落到 runtime/VERSION 供追溯（运行期 --version 走
# importlib.metadata，同一来源——pyproject.toml）
VERSION="$("$VENV_PYTHON" -c "import importlib.metadata; print(importlib.metadata.version('nova-harness'))")"
echo "==> nova-harness 版本: $VERSION"

# nova_base 数据随行：整条 bundle 目录（后端半区 + 前端渲染器源文件）打进去，
# 但剔除 node_modules / 缓存（前端半区只有 devDependencies，运行期渲染器的
# 宿主依赖经 virtualModules 直供，磁盘 node_modules 不需要）
STAGED_BUNDLE="$WORK_DIR/nova_base"
echo "==> 暂存 bundles/nova_base（剔除 node_modules/__pycache__/.pytest_cache）"
(cd "$REPO_ROOT/bundles" && tar -cf - \
    --exclude='node_modules' \
    --exclude='__pycache__' \
    --exclude='.pytest_cache' \
    --exclude='.git' \
    nova_base) | (mkdir -p "$WORK_DIR" && cd "$WORK_DIR" && tar -xf -)

echo "==> PyInstaller onedir 冻结（入口 nova_harness.cli.backend:main）"
"$VENV_PYTHON" -m PyInstaller \
    --onedir \
    --name nova-server \
    --clean --noconfirm \
    --collect-all nova_ai \
    --collect-all nova_agent \
    --collect-all nova_harness \
    --collect-all nova_base \
    --add-data "$(nat "$STAGED_BUNDLE")${ADD_DATA_SEP}bundles/nova_base" \
    --distpath "$(nat "$WORK_DIR/dist")" \
    --workpath "$(nat "$WORK_DIR/build")" \
    --specpath "$(nat "$WORK_DIR")" \
    "$(nat "$REPO_ROOT/packages/nova_harness/src/nova_harness/cli/backend.py")"

echo "==> 整理产物到 $OUTPUT_DIR/runtime/"
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"
mv "$WORK_DIR/dist/nova-server" "$OUTPUT_DIR/runtime"
echo "$VERSION" > "$OUTPUT_DIR/runtime/VERSION"

# 构建期自检：冻结产物的 --version 必须命中打包元数据
echo "==> 自检：runtime/nova-server --version"
if [[ -x "$OUTPUT_DIR/runtime/nova-server.exe" ]]; then
    SERVER_BIN="$OUTPUT_DIR/runtime/nova-server.exe"
else
    SERVER_BIN="$OUTPUT_DIR/runtime/nova-server"
fi
REPORTED="$("$SERVER_BIN" --version)"
echo "    $REPORTED"
if [[ "$REPORTED" != "nova-server $VERSION" ]]; then
    echo "错误：版本戳自检失败（期望 nova-server ${VERSION}，实得 ${REPORTED}）"
    exit 1
fi

echo ""
echo "==> 后端构建完成: ${OUTPUT_DIR}/runtime/（nova-harness ${VERSION}）"
du -sh "$OUTPUT_DIR/runtime"
