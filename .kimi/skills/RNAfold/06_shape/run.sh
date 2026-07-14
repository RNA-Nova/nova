#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"
cd "$SCRIPT_DIR/outputs"
RNAfold --shape="$SCRIPT_DIR/inputs/06_shape.dat" --shapeMethod=D --shapeConversion=O "$SCRIPT_DIR/inputs/06_shape.fa" > stdout.txt 2> stderr.txt
