#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"
cd "$SCRIPT_DIR/outputs"
tRNAscan-SE -G -Q \
    -o "$SCRIPT_DIR/outputs/metagenome_tRNA.out" \
    -a "$SCRIPT_DIR/outputs/metagenome_tRNA.fa" \
    "$SCRIPT_DIR/inputs/04_metagenome.fa" > stdout.txt 2> stderr.txt
