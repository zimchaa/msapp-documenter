#!/usr/bin/env bash
# Creates the Python environment used by msapp-documenter (idempotent).
set -euo pipefail
VENV="${MSAPP_DOCUMENTER_VENV:-$HOME/.local/share/msapp-documenter/venv}"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
if ! "$VENV/bin/python" -c "import pptx, yaml" 2>/dev/null; then
  "$VENV/bin/python" -m pip install -q python-pptx PyYAML
fi
echo "ready: $VENV/bin/python"
