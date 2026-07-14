#!/bin/bash
#
# run_all.sh
#
# 一键运行所有算法示例：
#   - RNAfold 全部场景
#   - tRNAscan-SE 全部场景
#   - R2DT 全部场景
#
# 目录结构为：算法/场景/（可选子场景/）inputs/ + run.sh
# 每个 run.sh 运行后，结果保存在其所在目录的 outputs/ 子目录中。
#
# 用法：
#   conda activate tools
#   ./run_all.sh
#

set -uo pipefail

BASEDIR="$(cd "$(dirname "$0")" && pwd)"
FAILED=()

run_script() {
    local run_sh="$1"
    local scenario_dir
    scenario_dir=$(dirname "$run_sh")
    local rel_path="${scenario_dir#$BASEDIR/}"
    echo "[$rel_path] running ..."
    if (cd "$scenario_dir" && bash run.sh); then
        echo "  -> results in $scenario_dir/outputs"
    else
        echo "  -> FAILED"
        FAILED+=("$rel_path")
    fi
}

# 按字典序递归执行所有 run.sh
while IFS= read -r run_sh; do
    run_script "$run_sh"
done < <(find "$BASEDIR"/RNAfold "$BASEDIR"/tRNAscan-SE "$BASEDIR"/r2dt -name run.sh -type f | sort)

echo ""
echo "全部场景执行完成。"
if [ ${#FAILED[@]} -gt 0 ]; then
    echo ""
    echo "以下场景运行失败："
    for f in "${FAILED[@]}"; do
        echo "  - $f"
    done
    exit 1
fi
