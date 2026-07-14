#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"
cd "$SCRIPT_DIR/outputs"
RNAfold --nsp="-GA" "$SCRIPT_DIR/inputs/08_04_noncanonical.fa" > stdout.txt 2> stderr.txt
