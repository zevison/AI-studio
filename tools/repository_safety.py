"""Credential-free source/release preflight. Findings never include secret text.

Pattern scanning cannot prove absence of every unknown secret. Local migration
also compares against the actual DPAPI vault and scans every Git object.
"""
import argparse,json,re,shutil,subprocess,zipfile
from pathlib import Path

PATTERNS={
    'openai_key':rb'\bsk-[A-Za-z0-9_-]{24,}',
    'github_token':rb'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{40,})',
    'aws_key':rb'\bAKIA[A-Z0-9]{16}\b',
    'oss_key':rb'\bLTAI[A-Za-z0-9]{12,}\b',
    'private_key':rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/=\r\n]{48,32768}-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
}
PREFIXES={'openai_key':(b'sk-',),'github_token':(b'gh',b'github_pat_'),
          'aws_key':(b'AKIA',),'oss_key':(b'LTAI',),'private_key':(b'-----BEGIN ',)}
# Exact public SSH/FIDO algorithm identifiers embedded in TLS/media binaries
# are not credentials. A token with any additional suffix still fails.
PUBLIC_IDENTIFIERS={b'sk-'+suffix for suffix in (
    b'ssh-ed25519',b'ssh-ed25519-cert-v01',b'ecdsa-sha2-nistp256',b'ecdsa-sha2-nistp256-cert-v01')}

def unsafe_source_path(name):
    parts=Path(name).parts
    base=parts[-1] if parts else ''
    return (any(x in {'.git','.venv','__pycache__','node_modules','install_logs'} for x in parts)
        or base in {'.env','secrets.json','secrets.dpapi','profile.json'}
        or (base.startswith('.env.') and not base.endswith('.example'))
        or Path(name).suffix.lower() in {'.db','.sqlite','.sqlite3','.dpapi','.pem','.p12','.key','.log','.pid'}
        or (parts and parts[0] in {'data','build','outputs','dist'}))

def scan_files(root,names,*,private_paths=True):
    root=Path(root);findings=[];count=0;members=0;total=0
    def check(stream,label):
        nonlocal total
        tail=b'';seen=set()
        while block:=stream.read(1024*1024):
            total+=len(block)
            if total>4*1024**3:raise ValueError('Safety scan byte limit exceeded')
            data=tail+block
            for kind,pattern in PATTERNS.items():
                matches=re.finditer(pattern,data) if kind not in seen and any(prefix in data for prefix in PREFIXES[kind]) else ()
                if any(m[0] not in PUBLIC_IDENTIFIERS for m in matches):
                    findings.append({'path':label,'kind':kind});seen.add(kind)
            tail=data[-65536:]
    for name in names:
        file=root/name
        if file.is_symlink():findings.append({'path':name,'kind':'source_link'});continue
        if not file.is_file():findings.append({'path':name,'kind':'missing_file'});continue
        count+=1
        if private_paths and unsafe_source_path(name):findings.append({'path':name,'kind':'private_path'})
        if file.suffix.lower()=='.zip':
            with zipfile.ZipFile(file) as bundle:
                for item in bundle.infolist():
                    if item.is_dir():continue
                    if item.file_size>600*1024**2:raise ValueError('Safety scan entry too large')
                    members+=1
                    with bundle.open(item) as stream:check(stream,name+'!/'+item.filename)
        else:
            with file.open('rb') as stream:check(stream,name)
    return {'files':count,'archive_members':members,'bytes_scanned':total,'findings':findings,
        'scope':'Known credential patterns and selected file paths; no private vault read or service calls',
        'unknown_secret_absence_not_guaranteed':True}

def scan_tree(root):
    root=Path(root)
    names=[p.relative_to(root).as_posix() for p in sorted(root.rglob('*')) if p.is_file()]
    result=scan_files(root,names,private_paths=False)
    # Third-party archives may contain npm runtime/data directories. Apply local
    # credential/user-data path rules only to the actual application sources.
    for name in names:
        relative=name.removeprefix('Studio/')
        if unsafe_source_path(relative):result['findings'].append({'path':name,'kind':'private_path'})
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tree',type=Path)
    parser.add_argument('--git',default=shutil.which('git'))
    args=parser.parse_args()
    if args.tree:result=scan_tree(args.tree)
    else:
        root=Path(__file__).resolve().parents[1]
        git=args.git
        if not git:raise SystemExit('Git is required')
        names=sorted({p.decode('utf8') for p in subprocess.check_output([git,
            '-c','safe.directory='+str(root).replace('\\','/'),'-C',str(root),
            'ls-files','--cached','--others','--exclude-standard','-z']).split(b'\0') if p})
        result=scan_files(root,names)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(bool(result['findings']))
