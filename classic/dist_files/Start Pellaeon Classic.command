#!/bin/bash
cd "$(dirname "$0")"
python3 run.py || { echo "Python 3 is needed: https://www.python.org/downloads/"; read -p "Press Enter"; }
