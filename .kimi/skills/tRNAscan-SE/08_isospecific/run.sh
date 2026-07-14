#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"
cd "$SCRIPT_DIR/outputs"
tRNAscan-SE -B -Q \
    -s "$SCRIPT_DIR/outputs/isospecific.out" \
    -o "$SCRIPT_DIR/outputs/tRNA.out" \
    -m "$SCRIPT_DIR/outputs/tRNA.stats" \
    "$SCRIPT_DIR/inputs/08_isospecific.fa" > stdout.txt 2> stderr.txt
