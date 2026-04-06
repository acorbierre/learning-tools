#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "Installation des dépendances..."
pip install -r requirements.txt -q

echo ""
echo "Démarrage des serveurs..."

python3 app.py &
python3 prof-virtuel.py &
python3 hub.py &
python3 hub2.py &

echo ""
echo "  Hub 1 — Assistant Formation  → http://127.0.0.1:5000"
echo "  Hub 2 — Claude + RF = <3     → http://127.0.0.1:5004"
echo "  Prof Virtuel                 → http://127.0.0.1:5001"
echo "  Synthèse Audio               → http://127.0.0.1:5003"
echo ""
echo "Ctrl+C pour tout arrêter"
echo ""

sleep 1 && open http://127.0.0.1:5000

wait
