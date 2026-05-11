#!/bin/bash
cd "$(dirname "$0")"

git pull

python3.11 -m pip install -r requirements.txt

python3.11 app.py >/dev/null 2>&1