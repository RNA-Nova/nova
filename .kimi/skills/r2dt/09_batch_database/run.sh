#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"

IMAGE="rnacentral/r2dt"
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "Pulling $IMAGE ..."
    docker pull "$IMAGE"
fi

# 场景九：数据库标准结构生成 - 批量处理多个 FASTA
docker run --rm \
    -v "$SCRIPT_DIR:/rna/r2dt/temp" \
    "$IMAGE" \
    bash -c 'for f in /rna/r2dt/temp/inputs/*.fa; do base=$(basename "$f" .fa); echo "processing $base ..."; r2dt.py draw "$f" "/rna/r2dt/temp/outputs/${base}"; done' \
    > "$SCRIPT_DIR/outputs/stdout.txt" 2> "$SCRIPT_DIR/outputs/stderr.txt"

# 清理 R2DT 在 inputs 中生成的临时索引文件
rm -f "$SCRIPT_DIR/inputs"/*.ssi
