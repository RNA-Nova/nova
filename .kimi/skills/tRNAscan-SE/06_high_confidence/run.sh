#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"
cd "$SCRIPT_DIR/outputs"
mkdir -p "$SCRIPT_DIR/outputs/filtered"
tRNAscan-SE -E --detail -Q \
    -o "$SCRIPT_DIR/outputs/tRNA.out" \
    -f "$SCRIPT_DIR/outputs/tRNA.ss" \
    "$SCRIPT_DIR/inputs/06_high_confidence.fa" \
    > "$SCRIPT_DIR/outputs/stdout_scan.txt" 2> "$SCRIPT_DIR/outputs/stderr_scan.txt"
EukHighConfidenceFilter \
    -i "$SCRIPT_DIR/outputs/tRNA.out" \
    -s "$SCRIPT_DIR/outputs/tRNA.ss" \
    -o "$SCRIPT_DIR/outputs/filtered" \
    -p genome \
    > "$SCRIPT_DIR/outputs/stdout_filter.txt" 2> "$SCRIPT_DIR/outputs/stderr_filter.txt"
