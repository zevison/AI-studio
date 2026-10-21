"""Free regression entry usable in Windows checkouts and Linux Cloud tasks."""
import argparse,json,os,shutil,subprocess,sys,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def run():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node',default=shutil.which('node'))
    parser.add_argument('--evidence',type=Path,default=ROOT/'build/checks')
    args=parser.parse_args()
    if not args.node:raise SystemExit('Select the reviewed Node in tools/windows-runtime.lock.json or pass --node; no model key needed.')
    proof=args.evidence.resolve();proof.mkdir(parents=True,exist_ok=True)
    # Logs can live in a deep evidence directory; Windows fixtures must not
    # inherit that arbitrary depth and exceed SQLite/ZIP path limits.
    scratch=ROOT/'build';scratch.mkdir(exist_ok=True)
    test_temp=Path(tempfile.mkdtemp(prefix='tests-',dir=scratch))
    profile=Path(tempfile.mkdtemp(prefix='profile-',dir=proof))
    env=os.environ.copy();env.update(LOCALAPPDATA=str(profile),XDG_CONFIG_HOME=str(profile),
        STUDIO_PROFILE_HOME=str(profile/'SeedanceStudio'),STUDIO_DATA_HOME=str(profile/'application'),
        OPENLUX_API_KEY='',OPENLUX_ACCOUNT_ACCESS_TOKEN='',PYTHONUTF8='1',PYTHONIOENCODING='utf-8',
        PYTHONPATH=str(ROOT)+os.pathsep+env.get('PYTHONPATH',''))
    sdk=ROOT/'tools/hypit_provider';node=str(Path(args.node).resolve())
    compiled=proof/'provider-build'
    commands=[
        ('python',[sys.executable,'-m','pytest',str(ROOT/'tests_integrated'),str(sdk/'tests'),'-q','--basetemp',str(test_temp)],ROOT),
        ('frontend',[node,'--test',*[str(p) for p in sorted((ROOT/'tests_integrated').glob('*.test.cjs'))]],ROOT),
        ('typescript',[node,str(sdk/'node_modules/typescript/bin/tsc'),'-p',str(sdk/'tsconfig.json'),'--outDir',str(compiled)],sdk),
        ('sdk',[node,'--import','./tests/sdk-bootstrap.mjs','--test',*[str(p) for p in sorted((sdk/'dist/tests').glob('*.test.js'))]],sdk),
    ]
    results={}
    for label,command,cwd in commands:
        print('Running '+label+' (no paid calls)',flush=True)
        with (proof/(label+'.log')).open('w',encoding='utf8') as log:
            result=subprocess.run(command,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=360)
        results[label]={'passed':result.returncode==0,'log':label+'.log'}
        (proof/'summary.json').write_text(json.dumps(results,indent=2),encoding='utf8')
        if result.returncode:raise SystemExit('FAILED '+label+'; see '+str(proof/(label+'.log')))
    for p in compiled.rglob('*'):
        if p.is_file() and p.read_bytes()!=(sdk/'dist'/p.relative_to(compiled)).read_bytes():
            raise SystemExit('Rebuilt SDK differs from versioned dist: '+p.name)
    print('PASS: Python/frontend/TypeScript/SDK; dist byte equality; zero paid calls.',flush=True)

if __name__=='__main__':run()
