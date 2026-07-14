#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"
cd "$SCRIPT_DIR/outputs"
RNAfold --gquad "$SCRIPT_DIR/inputs/07_gquad.fa" > stdout.txt 2> stderr.txt
