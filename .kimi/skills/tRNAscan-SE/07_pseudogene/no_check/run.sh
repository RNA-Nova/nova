#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"
cd "$SCRIPT_DIR/outputs"
tRNAscan-SE -E -D -Q \
    -o "$SCRIPT_DIR/outputs/tRNA_no_pseudo_check.out" \
    -f "$SCRIPT_DIR/outputs/tRNA.ss" \
    "$SCRIPT_DIR/inputs/07_pseudogene.fa" > stdout.txt 2> stderr.txt
