#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 --version
node --version
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -r requirements-dev.txt
npm ci --ignore-scripts --prefix tools/hypit_provider --no-audit --no-fund
npm run build:offline --prefix tools/hypit_provider
echo 'Prepared free development tools. No API keys or production access required.'
