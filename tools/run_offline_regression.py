"""One developer command; records source identity before/after all offline suites."""
import argparse,json,os,subprocess,sys
from pathlib import Path
from release_candidate import source_hash


def run():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    proof=args.evidence.resolve();proof.mkdir(parents=True,exist_ok=True)
    before=source_hash(root)
    env=os.environ.copy();env['LOCALAPPDATA']=str(proof/'isolated-profile')
    env.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8',PYTHONUNBUFFERED='1')
    env['OPENLUX_API_KEY']='';env['OPENLUX_ACCOUNT_ACCESS_TOKEN']=''
    env['PYTHONPATH']=str(root)+os.pathsep+env.get('PYTHONPATH','')
    sdk=root/'tools/hypit_provider';node=str(args.node.resolve())
    commands=[
        ('full-regression-2.log',[sys.executable,'-m','pytest',str(root/'tests_integrated'),str(sdk/'tests'),'-q','--basetemp',str(proof/'pytest-temp')],root),
        ('frontend.log',[node,'--test',*(str(p) for p in sorted((root/'tests_integrated').glob('*.test.cjs')))],root),
        ('provider-compile.log',[node,str(sdk/'node_modules/typescript/bin/tsc'),'-p',str(sdk/'tsconfig.json'),'--outDir',str(proof/'provider-build')],sdk),
        ('official-sdk-tests.log',[node,'--import','./tests/sdk-bootstrap.mjs','--test',*(str(p) for p in sorted((sdk/'dist/tests').glob('*.test.js')))],sdk),
    ]
    for name,command,cwd in commands:
        with (proof/name).open('w',encoding='utf8') as log:
            result=subprocess.run(command,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=300)
        if result.returncode:raise RuntimeError('Offline regression failed: '+name)
        print('PASS '+name,flush=True)
    for p in (proof/'provider-build').rglob('*'):
        if p.is_file() and p.read_bytes()!=(sdk/'dist'/p.relative_to(proof/'provider-build')).read_bytes():
            raise RuntimeError('Compiled SDK differs from shipped dist: '+p.name)
    after=source_hash(root)
    if before!=after:raise RuntimeError('Source changed during regression; rerun before building')
    (proof/'regression-source.json').write_text(json.dumps({'source_sha256':after,'all_suites_passed':True,'paid_calls':0},indent=2),encoding='utf8')
    print('All suites passed; source identity recorded')


if __name__=='__main__':run()
