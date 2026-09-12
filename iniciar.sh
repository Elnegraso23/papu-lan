#!/bin/bash
# Iniciar Papu-LAN en la red local
cd "$(dirname "$0")"
python3 app.py --port 8000
