from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def owned(p):
    generated={'.git','.venv','node_modules','build','outputs','data','install_logs','__pycache__','.pytest_cache'}
    return p.is_file() and not generated.intersection(p.relative_to(ROOT).parts)
cmds=[p for p in ROOT.rglob('*') if owned(p) and p.suffix.lower() in {'.cmd','.bat'}]
ps1s=[p for p in ROOT.rglob('*.ps1') if owned(p)]
vbs=[p for p in ROOT.rglob('*.vbs') if owned(p)]
assert sorted(x.name for x in ROOT.glob('*.cmd'))==['00_START.cmd','01_INSTALL.cmd']
assert cmds and ps1s and vbs
for p in cmds:
    b=p.read_bytes()
    assert b'\r\n' in b and b'\n' not in b.replace(b'\r\n',b''), f'CMD missing CRLF: {p}'
    assert not b.startswith((b'\xef\xbb\xbf',b'\xff\xfe')), f'CMD should have no BOM: {p}'
for p in ps1s:
    b=p.read_bytes()
    assert b.startswith(b'\xef\xbb\xbf'), f'PS1 Windows PowerShell 5.1 requires BOM: {p}'
    assert b'\n' not in b.replace(b'\r\n',b''), f'PS1 missing CRLF: {p}'
for p in vbs:
    b=p.read_bytes()
    assert b.startswith(b'\xff\xfe'), f'VBS UTF16 BOM: {p}'
    assert '\r\n' in b.decode('utf-16'),f'VBS CRLF: {p}'
entry=(ROOT/'01_INSTALL.cmd').read_text('ascii')
assert 'powershell.exe' in entry
assert 'tools\\install_new_pc.ps1' in entry
assert 'One-click Windows setup' in entry
assert '00_INSTALL_NEW_PC.cmd' not in entry
print(f'PASS Windows release-format: CMD/BAT {len(cmds)}, PS1 {len(ps1s)}, VBS {len(vbs)}')
