#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"
cd "$SCRIPT_DIR/outputs"
RNAfold -p "$SCRIPT_DIR/inputs/02_partition.fa" > stdout.txt 2> stderr.txt
