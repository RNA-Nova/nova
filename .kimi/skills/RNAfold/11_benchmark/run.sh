#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"
cd "$SCRIPT_DIR/outputs"
RNAfold --benchmark --bm-output="benchmark_results.txt" "$SCRIPT_DIR/inputs/11_benchmark.fa" > stdout.txt 2> stderr.txt
