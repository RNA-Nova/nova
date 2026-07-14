#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"

IMAGE="rnacentral/r2dt"
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "Pulling $IMAGE ..."
    docker pull "$IMAGE"
fi

# 场景三：特定 RNA 家族可视化 - GtRNAdb tRNA
docker run --rm \
    -v "$SCRIPT_DIR:/rna/r2dt/temp" \
    "$IMAGE" \
    r2dt.py gtrnadb draw /rna/r2dt/temp/inputs/03_gtrnadb.fa /rna/r2dt/temp/outputs \
    > "$SCRIPT_DIR/outputs/stdout.txt" 2> "$SCRIPT_DIR/outputs/stderr.txt"

# 清理 R2DT 在 inputs 中生成的临时索引文件
rm -f "$SCRIPT_DIR/inputs"/*.ssi
