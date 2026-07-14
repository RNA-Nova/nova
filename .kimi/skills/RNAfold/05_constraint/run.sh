#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"
cd "$SCRIPT_DIR/outputs"
RNAfold -C --enforceConstraint "$SCRIPT_DIR/inputs/05_constraint.fa" > stdout.txt 2> stderr.txt
