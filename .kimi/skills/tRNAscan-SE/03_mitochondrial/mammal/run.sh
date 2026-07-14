#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"
cd "$SCRIPT_DIR/outputs"
tRNAscan-SE -M mammal -Q \
    -o "$SCRIPT_DIR/outputs/mt_tRNA.out" \
    -f "$SCRIPT_DIR/outputs/mt_tRNA.ss" \
    "$SCRIPT_DIR/inputs/03_mitochondrial.fa" > stdout.txt 2> stderr.txt
