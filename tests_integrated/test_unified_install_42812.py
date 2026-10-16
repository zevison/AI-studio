"""Offline contract tests for single 01 installer. Never uses network or pip."""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import bootstrap_install as install
import install_analysis_stack


def test_single_entry():
    one=(ROOT/'01_INSTALL.cmd').read_text(encoding='utf-8')
    ps=(ROOT/'tools/install_new_pc.ps1').read_text(encoding='utf-8')
    assert 'tools\\install_new_pc.ps1' in one
    assert not (ROOT/'00_INSTALL_NEW_PC.cmd').exists()
    assert "bootstrap_install.py" in ps
    for filename in ('08_安装本地逐词增强.cmd','10_安装完整本地分析能力.cmd',
                     '13_INSTALL_TESSERACT.cmd','14_INSTALL_BRIGHT_DOWNLOADER.cmd'):
        assert not (ROOT/filename).exists(), filename
    assert 'install_complete_analysis()' in (ROOT/'tools/bootstrap_install.py').read_text(encoding='utf-8')
    assert 'install_original_post_downloader()' in (ROOT/'tools/bootstrap_install.py').read_text(encoding='utf-8')
    assert 'SEEDANCE_INSTALL_FULL_ANALYSIS' not in (ROOT/'tools/bootstrap_install.py').read_text(encoding='utf-8')
    print('PASS single 01 entry, no separate installers or hidden full-analysis switch')


def test_main_runs_every_phase_and_partial_is_explicit():
    calls=[]
    def record(k, v=None):
        calls.append(k)
        return v
    with patch.object(install, 'prepare_venv', side_effect=lambda:record('venv','ok')), \
         patch.object(install, 'ensure_requirements', side_effect=lambda:record('core')), \
         patch.object(install, 'run', side_effect=lambda cmd:record('timezone',0)), \
         patch.object(install_analysis_stack, 'studio_is_running', return_value=False), \
         patch.object(install, 'install_complete_analysis', side_effect=lambda:record('analysis',10)), \
         patch.object(install, 'install_original_post_downloader', side_effect=lambda:record('downloader',True)), \
         patch.object(install, 'prepare_cloudflared', side_effect=lambda:record('cloud')), \
         patch.object(install, 'repair_scenedetect', side_effect=lambda:record('repair',True)), \
         patch.object(install, 'capability_report', side_effect=lambda **kw:record('report',True)):
        assert install.main()==0
        assert calls==['venv','core','timezone','analysis','downloader','cloud','repair','report'], calls
        calls.clear()
        assert install.main()==0
        assert 'analysis' in calls, 'repeat installer must recheck incomplete analysis'
        print('PASS all phases automatic, partial enhancement transparent, rerunnable')


def test_missing_required_is_failure():
    with patch.object(install, 'prepare_venv', return_value='ok'), \
         patch.object(install, 'ensure_requirements'), \
         patch.object(install, 'run', return_value=0), \
         patch.object(install_analysis_stack, 'studio_is_running', return_value=False), \
         patch.object(install, 'install_complete_analysis', return_value=10), \
         patch.object(install, 'install_original_post_downloader', return_value=False), \
         patch.object(install, 'prepare_cloudflared'), \
         patch.object(install, 'repair_scenedetect', return_value=False), \
         patch.object(install, 'capability_report', return_value=False):
        assert install.main()==10
    print('PASS no false installation success if required components are missing')


def test_running_studio_blocks_install():
    with patch.object(install, 'prepare_venv', return_value='ok'), \
         patch.object(install, 'ensure_requirements'), \
         patch.object(install, 'run', return_value=0), \
         patch.object(install_analysis_stack, 'studio_is_running', return_value=True), \
         patch.object(install, 'install_complete_analysis', side_effect=AssertionError('must not run')):
        assert install.main()==3
    print('PASS active studio blocks native dependency replacement')


if __name__=='__main__':
    test_single_entry()
    test_main_runs_every_phase_and_partial_is_explicit()
    test_missing_required_is_failure()
    test_running_studio_blocks_install()
    print('PASS unified installer offline suite')
