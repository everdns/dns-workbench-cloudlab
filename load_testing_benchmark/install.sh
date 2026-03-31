#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

echo "=== Installing Python dependencies for load_testing_benchmark_py ==="

# Check for Python 3
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null && python --version 2>&1 | grep -q "Python 3"; then
    PYTHON=python
else
    echo "Error: Python 3 is required but not found."
    exit 1
fi

echo "Using $($PYTHON --version)"

# Create virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment in $VENV_DIR ..."
    $PYTHON -m venv "$VENV_DIR"
else
    echo "Virtual environment already exists at $VENV_DIR"
fi

# Activate and install
source "$VENV_DIR/bin/activate"

echo "Upgrading pip ..."
pip install --upgrade pip

echo "Installing requirements ..."
pip install -r "$SCRIPT_DIR/requirements.txt"

echo ""
echo "=== Installation complete ==="
echo "Activate the environment with:"
echo "  source $VENV_DIR/bin/activate"
