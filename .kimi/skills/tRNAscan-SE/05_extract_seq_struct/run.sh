#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"
cd "$SCRIPT_DIR/outputs"
tRNAscan-SE -B -Q \
    -o "$SCRIPT_DIR/outputs/tRNA.out" \
    -f "$SCRIPT_DIR/outputs/tRNA.ss" \
    -a "$SCRIPT_DIR/outputs/tRNA.fa" \
    -b "$SCRIPT_DIR/outputs/tRNA.bed" \
    "$SCRIPT_DIR/inputs/05_extract_seq_struct.fa" > stdout.txt 2> stderr.txt
