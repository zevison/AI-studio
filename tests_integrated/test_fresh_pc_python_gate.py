"""Exercise the real PowerShell interpreter probe with compatible/old/hung processes."""
import os
import subprocess
from pathlib import Path
import pytest

@pytest.mark.skipif(os.name != 'nt', reason='Real Windows PowerShell process test')
def test_real_interpreter_probe_has_bounded_timeout_and_rejects_wrong_python(tmp_path):
    installer=Path(__file__).resolve().parents[1]/'tools/install_new_pc.ps1'
    script=tmp_path/'probe.ps1'
    script.write_text(r'''
param($Installer,$Temp)
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($Installer,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Installer PowerShell syntax error'}
$func=$ast.Find({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Test-Python'},$true)
Invoke-Expression $func.Extent.Text
function Log-Line($msg) {Write-Host $msg}
$fake=Join-Path $Temp 'fake-python.exe'
Add-Type -TypeDefinition 'using System; using System.Threading; public class FakePython {public static void Main(string[] args) { if(args[0]=="hang") { Thread.Sleep(60000); } else { Console.WriteLine(args[0]=="old" ? "3 11 64" : args[0]=="small" ? "3 12 32" : "3 12 64"); } }}' -OutputAssembly $fake -OutputType ConsoleApplication
if(-not (Test-Python $fake @('good'))) {throw 'Compatible interpreter rejected'}
if(Test-Python $fake @('old')) {throw 'Old interpreter accepted'}
if(Test-Python $fake @('small')) {throw '32-bit interpreter accepted'}
$timer=[Diagnostics.Stopwatch]::StartNew()
if(Test-Python $fake @('hang')) {throw 'Hung interpreter accepted'}
if($timer.Elapsed.TotalSeconds -gt 20) {throw 'Interpreter timeout ineffective'}
Write-Output 'PASS: compatible, old, 32-bit and hung interpreter cases'
''', encoding='utf-8-sig')
    powershell=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    result=subprocess.run([str(powershell),'-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),str(installer),str(tmp_path)],capture_output=True,text=True,errors='replace',timeout=45)
    assert result.returncode==0, result.stdout+'\n'+result.stderr
    assert 'PASS:' in result.stdout
