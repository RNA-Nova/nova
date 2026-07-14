#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/outputs"
cd "$SCRIPT_DIR/outputs"
RNAfold --motif="GAUACCAG&CCCUUGGCAGC,(...((((&)...)))...),-9.22" "$SCRIPT_DIR/inputs/08_01_ligand.fa" > stdout.txt 2> stderr.txt
