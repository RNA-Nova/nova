#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"
cd "$SCRIPT_DIR/outputs"
RNAfold --jobs=2 --auto-id --id-prefix="batch" --id-digits=4 "$SCRIPT_DIR/inputs/09_batch.fa" > stdout.txt 2> stderr.txt
