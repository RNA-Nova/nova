#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"
cd "$SCRIPT_DIR/outputs"
RNAfold --maxBPspan=150 "$SCRIPT_DIR/inputs/12_local.fa" > stdout.txt 2> stderr.txt
