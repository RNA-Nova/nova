#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"
cd "$SCRIPT_DIR/outputs"
tRNAscan-SE -B -Q \
    -o "$SCRIPT_DIR/outputs/tRNA.out" \
    -f "$SCRIPT_DIR/outputs/tRNA.ss" \
    -m "$SCRIPT_DIR/outputs/tRNA.stats" \
    "$SCRIPT_DIR/inputs/01_prokaryotic.fa" > stdout.txt 2> stderr.txt
