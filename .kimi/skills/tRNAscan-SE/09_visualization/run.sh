#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"
cd "$SCRIPT_DIR/outputs"
tRNAscan-SE -B -Q \
    -f "$SCRIPT_DIR/outputs/tRNA.ss" \
    -a "$SCRIPT_DIR/outputs/tRNA.fa" \
    "$SCRIPT_DIR/inputs/09_visualization.fa" > stdout.txt 2> stderr.txt
