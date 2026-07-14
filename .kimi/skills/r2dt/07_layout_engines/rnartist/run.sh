#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"

IMAGE="rnacentral/r2dt"
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "Pulling $IMAGE ..."
    docker pull "$IMAGE"
fi

# 场景七：多布局引擎比较 - RNArtist
docker run --rm \
    -v "$SCRIPT_DIR:/rna/r2dt/temp" \
    "$IMAGE" \
    r2dt.py templatefree /rna/r2dt/temp/inputs/07_layout.fa /rna/r2dt/temp/outputs --rnartist \
    > "$SCRIPT_DIR/outputs/stdout.txt" 2> "$SCRIPT_DIR/outputs/stderr.txt"

# 清理 R2DT 在 inputs 中生成的临时索引文件
rm -f "$SCRIPT_DIR/inputs"/*.ssi
