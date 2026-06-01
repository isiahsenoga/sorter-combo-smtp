#!/usr/bin/env bash
cd "$(dirname "$0")"

# find python3 or python
PY=""
command -v python3 &>/dev/null && PY=python3
[ -z "$PY" ] && command -v python &>/dev/null && PY=python
if [ -z "$PY" ]; then
    echo "Python not found. Please install Python 3.9+ from https://python.org"
    exit 1
fi

echo "Checking dependencies..."
echo ""

# auto-install missing requirements
if ! $PY -c "import PySide6, tqdm" &>/dev/null; then
    echo "Installing requirements (PySide6, tqdm)..."
    if ! $PY -m pip install -r requirements.txt -q; then
        echo ""
        echo "Failed to install requirements. Please run:"
        echo "  $PY -m pip install -r requirements.txt"
        exit 1
    fi
    echo "Requirements installed successfully."
    echo ""
fi

echo " [1] GUI"
echo " [2] CLI"
echo ""
read -rp "Choice [1]: " choice

if [ "$choice" = "2" ]; then
    $PY main.py
else
    $PY gui.py
fi
