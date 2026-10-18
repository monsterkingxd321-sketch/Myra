#!/usr/bin/env bash
# One-click dependency setup for a Debian/Ubuntu VPS. Safe to re-run.

set -e

echo "=========================================================="
echo " Myra - VPS setup (Ubuntu / Debian)"
echo "=========================================================="

echo "-> Installing system packages (python, pip, venv, git, build tools)..."
sudo apt-get update -y
sudo apt-get install -y \
    python3 python3-pip python3-venv python3-dev \
    git build-essential gcc libffi-dev libssl-dev tmux

echo "-> Creating the virtual environment..."
[ -d "venv" ] || python3 -m venv venv

echo "-> Installing Python dependencies (this can take a minute)..."
# shellcheck disable=SC1091
source venv/bin/activate
pip install --upgrade pip wheel
pip install -r requirements.txt

echo "-> Checking the Telegram client layer (userbots need Kurigram)..."
python tools/check_pyrogram.py

if [ ! -f ".env" ]; then
    echo "-> No .env found, copying sample.env -> .env"
    cp sample.env .env
    echo "   !! Edit it now:  nano .env"
fi

echo "=========================================================="
echo " Setup complete."
echo "=========================================================="
echo ""
echo "Run it now:"
echo "  source venv/bin/activate && python3 -u -m Myra"
echo ""
echo "Or 24/7 in tmux:"
echo "  tmux new -s myra"
echo "  source venv/bin/activate && python3 -u -m Myra   (Ctrl+B then D to detach)"
echo ""
echo "Or as a systemd service:"
echo "  sudo cp myrabot.service /etc/systemd/system/myra.service"
echo "  sudo systemctl enable --now myra"
echo "=========================================================="
