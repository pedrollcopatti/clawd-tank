#!/bin/bash
# Build the Clawd Tank menu bar .app bundle.
# Usage: cd host && ./build.sh [--install]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> Building .app bundle..."
rm -rf build dist
.venv/bin/python setup.py py2app 2>&1 | tail -3

echo "==> Built: dist/Clawd Tank.app"

if [ "${1:-}" = "--install" ]; then
    echo "==> Installing to /Applications..."
    rm -rf "/Applications/Clawd Tank.app"
    cp -R "dist/Clawd Tank.app" "/Applications/Clawd Tank.app"
    echo "==> Installed to /Applications/Clawd Tank.app"
fi
