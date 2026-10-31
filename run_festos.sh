#!/usr/bin/env bash
# FestOS 1-Click Launcher for macOS & Linux
set -e

export PYTHONIOENCODING=utf-8

echo "======================================================================"
echo "   FESTOS: Aarohan 2026 Festival Control Operating System"
echo "   Epinoia 2026 Hackathon Submission - IIM Indore"
echo "======================================================================"
echo ""

if command -v python3 >/dev/null 2>&1; then
    PY_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PY_CMD="python"
else
    echo "[ERROR] Python 3.9+ was not found on your system PATH."
    echo "Please install Python 3.9 or higher."
    exit 1
fi

echo "[OK] Python runtime detected: $($PY_CMD --version)"
echo "[OK] Starting FestOS local control portal..."
echo ""
echo "----------------------------------------------------------------------"
echo "DEMO CREDENTIALS (All accounts use password: aarohan2026)"
echo "----------------------------------------------------------------------"
echo "  * Festival Director : ST08  (Full access, overrides, budget, audit)"
echo "  * Hospitality       : ST02  (Rooming, accessibility, transport, diet)"
echo "  * Registration Desk : ST01  (Check-in, duplicate merges, waterfall)"
echo "  * Events Team       : ST03  (Schedule Gantt, heats, judge assignments)"
echo "  * Logistics Head    : ST04  (Inventory movement, equipment shortages)"
echo "  * Merchandise Desk  : ST05  (Issue desk, fair-share quotas)"
echo "  * Food Coordinator  : ST06  (Diet max-flow, compliance, settlement)"
echo "  * Volunteer         : ST07  (Help desk lookup; sensitive data masked)"
echo "----------------------------------------------------------------------"
echo "[INFO] Your browser will open automatically at http://localhost:8000"
echo "[INFO] Press Ctrl+C in this terminal to stop the server."
echo "======================================================================"
echo ""

cd "$(dirname "$0")"
$PY_CMD run.py --port 8000
