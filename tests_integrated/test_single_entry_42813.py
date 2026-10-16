from pathlib import Path
import re
ROOT=Path(__file__).resolve().parents[1]
ps=(ROOT/'tools/install_new_pc.ps1').read_text(encoding='utf-8')
cmd=(ROOT/'01_INSTALL.cmd').read_text(encoding='utf-8')
vbs=(ROOT/'tools/launch_silent.vbs').read_text(encoding='utf-16')
startup=(ROOT/'00_START.cmd').read_text(encoding='ascii')
assert sorted(p.name for p in ROOT.glob('*INSTALL*.cmd')) == ['01_INSTALL.cmd']
assert sorted(x.name for x in ROOT.glob('*.cmd'))==['00_START.cmd','01_INSTALL.cmd'], 'One installer and one start CMD in root'
assert r'tools\install_new_pc.ps1' in cmd
assert 'System.Diagnostics.ProcessStartInfo' in ps
assert 'RedirectStandardOutput=$true' in ps
assert 'RedirectStandardError=$true' in ps
assert r'3\s+(10|11|12)\s+64' in ps
assert 'Python candidate check failed:' in ps
assert 'bootstrap_install.py' in ps
assert '01_INSTALL.cmd' in vbs
assert 'cmd.exe /c' not in vbs, 'Startup must never trigger installer'
assert 'start_silent.py' in vbs
assert 'launch_silent.vbs' in startup
for f in ('README_NEW_PC_CN.txt','00_先看这里.txt'):
    assert '01_INSTALL.cmd' in (ROOT/f).read_text(encoding='utf-8')
print('PASS one visible installer, non-installing startup, detailed Python probe')
