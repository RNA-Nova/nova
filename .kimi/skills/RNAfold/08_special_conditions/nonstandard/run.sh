#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"
cd "$SCRIPT_DIR/outputs"
RNAfold -T 25.0 --salt=0.1 "$SCRIPT_DIR/inputs/08_03_nonstandard.fa" > stdout.txt 2> stderr.txt
