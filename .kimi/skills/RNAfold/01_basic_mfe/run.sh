#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"
cd "$SCRIPT_DIR/outputs"
RNAfold  "$SCRIPT_DIR/inputs/01_basic_mfe.fa" > stdout.txt 2> stderr.txt
