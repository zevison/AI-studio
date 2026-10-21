#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 --version
node --version
python3 - <<'PY'
import json, subprocess, sys
from pathlib import Path
root=Path.cwd()
python=json.loads((root/'requirements/python-win-x64-py314-r39-candidate.json').read_text())['python']
node=json.loads((root/'tools/windows-runtime.lock.json').read_text())['node']['version']
if sys.version.split()[0]!=python or subprocess.check_output(['node','--version'],text=True).strip()!='v'+node:
    raise SystemExit('Select reviewed Python '+python+' and Node '+node+' before setup; no global runtime is replaced.')
PY
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -r requirements-dev.txt
npm ci --ignore-scripts --prefix tools/hypit_provider --no-audit --no-fund
npm run build:offline --prefix tools/hypit_provider
echo 'Prepared free development tools. No API keys or production access required.'
