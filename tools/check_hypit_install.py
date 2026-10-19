"""Offline mandatory checks for bundled Node, official Hypit and local renderer."""
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(command, cwd=ROOT, timeout=30):
    environment = {k:v for k,v in os.environ.items()
        if not any(word in k.upper() for word in ('KEY','TOKEN','SECRET','PASSWORD'))}
    result = subprocess.run(list(map(str,command)), cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
        capture_output=True, encoding='utf8', errors='replace', timeout=timeout,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        raise RuntimeError('Local runtime check failed: ' + (result.stdout+result.stderr)[-1200:])
    return result.stdout.strip()


def check():
    if os.name != 'nt' or platform.machine().upper() not in {'AMD64','X86_64'}:
        raise RuntimeError('This installation bundle requires 64-bit Windows x64')
    node=ROOT/'tools/node/node.exe'
    sdk=ROOT/'tools/hypit_provider'
    browser_cache=ROOT/'tools/hypit_browser'
    manifest=ROOT/'tools/hypit_bundle.json'
    if manifest.exists():
        doc=json.loads(manifest.read_text(encoding='utf8'))
        if doc.get('format')!='seedance.hypit-bundle@1':raise RuntimeError('Unknown runtime manifest')
        base=Path(os.environ['LOCALAPPDATA'])/'SeedanceStudio/assets/hypit-host'
        sdk=base/('sdk-'+doc['sdk']['sha256'][:12])
        browser_cache=base/('browser-'+doc['browser']['sha256'][:12])
    cli=sdk/'node_modules/@hypit/hypit/bin/hypit.mjs'
    browser=browser_cache/'chrome-headless-shell/win64-152.0.7928.2/chrome-headless-shell-win64/chrome-headless-shell.exe'
    required=[node,cli,browser,sdk/'dist/src/activation.js',sdk/'vendor/hypit-skill-v0.3.1/SKILL.md',
              sdk/'node_modules/@seedance-studio/openlux-provider/dist/src/activation.js']
    if any(not path.is_file() for path in required):
        raise RuntimeError('Hypit components missing; re-extract the COMPLETE installation ZIP')
    version=run([node,'--version'])
    if version != 'v22.16.0':raise RuntimeError('Unexpected bundled Node version')
    cli_version=run([node,cli,'--version'])
    if '0.3.1' not in cli_version:raise RuntimeError('Official Hypit SDK version mismatch')
    browser_version=run([browser,'--version'])
    if '152.0.7928.2' not in browser_version:raise RuntimeError('Bundled renderer browser version mismatch')
    native=run([node,'--input-type=module','-e',
        "import sharp from 'sharp'; import {version,transformSync} from 'esbuild'; "
        "transformSync('const x: number = 1',{loader:'ts'}); "
        "console.log(JSON.stringify({sharp:sharp.versions.sharp,esbuild:version}));"],cwd=sdk)
    native=json.loads(native)
    if native != {'sharp':'0.35.5','esbuild':'0.28.1'}:raise RuntimeError('Native security dependency versions mismatch')
    report={'status':'passed','node':version,'hypit':cli_version,'browser':browser_version,
            'native_modules':native,'model_api_calls':0,'scope':'offline runtime installation only'}
    directory=ROOT/'install_logs';directory.mkdir(exist_ok=True)
    (directory/'hypit_dependency_report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    return report


if __name__ == '__main__':
    try:
        print(json.dumps(check(),ensure_ascii=False,indent=2))
    except Exception as exc:
        print('HYPIT SETUP FAILED: '+str(exc),file=sys.stderr)
        raise SystemExit(3)
