#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"
cd "$SCRIPT_DIR/outputs"
RNAfold --MEA=1.0 "$SCRIPT_DIR/inputs/03_mea.fa" > stdout.txt 2> stderr.txt
