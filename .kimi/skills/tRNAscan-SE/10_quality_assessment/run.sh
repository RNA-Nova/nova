#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"
cd "$SCRIPT_DIR/outputs"
for f in "$SCRIPT_DIR"/inputs/10_genome_*.fa; do
    base=$(basename "$f" .fa)
    echo "scanning $base ..."
    tRNAscan-SE -B -Q -m "$SCRIPT_DIR/outputs/${base}.stats" "$f" \
        > "$SCRIPT_DIR/outputs/${base}.stdout" 2> "$SCRIPT_DIR/outputs/${base}.stderr"
done
