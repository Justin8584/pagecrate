#!/bin/bash
# PageCrate — Quick Setup
set -e

echo "=== PageCrate Setup ==="
echo ""

if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found. Install Python 3.8+ first."
    exit 1
fi

echo "[1/2] Installing Python dependencies..."
pip install -r requirements.txt

echo ""
echo "[2/2] Installing Chromium for Playwright..."
playwright install chromium

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Usage:"
echo "  python3 websaver.py --bbg system-design-interview --cleanup --pdf"
echo "  python3 websaver.py https://any-website.com/article --pdf"
echo ""
echo "PDF generation uses Playwright's built-in Chromium — no extra tools needed."
