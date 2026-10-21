"""Reject a genuinely stalled renderer in a separate frozen-program copy.

The release payload is never modified. Only synthetic localhost HTML is served.
"""
import argparse,json,os,shutil,sqlite3,subprocess,time
from pathlib import Path
from accept_desktop_exe import Application,clean_environment
from install_hypit_bundle import native_path


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--exe',required=True,type=Path);p.add_argument('--evidence',required=True,type=Path);a=p.parse_args()
    if os.name!='nt':p.error('Actual Windows is required')
    proof=a.evidence.resolve();proof.mkdir(parents=True,exist_ok=False)
    candidate=proof/'Broken UI';shutil.copytree(native_path(a.exe.parent.resolve()),native_path(candidate))
    static=candidate/'_internal/static'
    (static/'qa-stall.js').write_text('while (true) {}',encoding='utf8')
    (static/'index.html').write_text('<!doctype html><html><head><title>Seedance Studio QA</title><script src="/static/qa-stall.js"></script></head><body data-application="seedance-studio"></body></html>',encoding='utf8')
    home=proof/'Isolated User Data';ready=proof/'ready.json';stop=proof/'stop'
    report={'scope':'Actual frozen EXE with intentionally stalled localhost HTML in independent copy; not a released payload defect','paid_calls':0,'checks':[]}
    process=None;configured=False;healthy=False;gui_ready=False;info=None
    try:
        with (proof/'process.log').open('w',encoding='utf8') as log:
            started=time.monotonic()
            process=subprocess.Popen([str(candidate/a.exe.name),'--data-home',str(home),'--ready-file',str(ready),
                '--shutdown-file',str(stop),'--skip-migration-prompt','--gui-evidence',str(proof/'gui'),
                '--gui-load-timeout','5'],env=clean_environment(),stdout=log,stderr=subprocess.STDOUT)
            deadline=started+220
            while time.monotonic()<deadline:
                if ready.exists():
                    info=json.loads(ready.read_text('utf8'))
                    gui_ready|=info.get('gui')=='edgechromium-loaded'
                    if not configured and info.get('status')=='starting':
                        app=Application(a.exe,home,proof,'live');app.info=info
                        assert app.request('/api/bootstrap-health')['pid']==info['pid'];healthy=True
                        app.token=app.request('/api/hypit/session')['token']
                        doc=app.request('/api/configuration')
                        for row in doc['providers']:row['models']['chat']='gui-stall-preserved'
                        app.request('/api/configuration',{'document':doc,'expected_revision':doc['revision']});configured=True
                if process.poll() is not None:break
                time.sleep(.1)
            else:raise TimeoutError('Frozen GUI deadline failed to bound its own startup')
        assert process.returncode!=0
        assert healthy and configured and not gui_ready
        info=json.loads(ready.read_text('utf8'))
        assert info['status']=='failed' and info['gui']=='gui_load_timeout',info
        report['checks'].append({'name':'healthy backend never promoted stalled GUI to ready','status':'PASS',
            'exit_code':process.returncode,'total_seconds_including_backend':round(time.monotonic()-started,3),
            'configured_gui_deadline_seconds':5,'ready_file_state':info['gui']})
        restored=Application(a.exe,home,proof,'restored-original')
        try:
            restored.start();doc=restored.request('/api/configuration')
            assert all(row['models']['chat']=='gui-stall-preserved' for row in doc['providers'])
        finally:restored.close()
        with sqlite3.connect(home/'data/studio.db') as db:
            assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
            assert db.execute('SELECT count(*) FROM tasks').fetchone()[0]==0
            assert db.execute('SELECT count(*) FROM usage_events').fetchone()[0]==0
        report['checks'].append({'name':'original program restarts retained profile with valid database','status':'PASS'})
        report['status']='PASS'
    except BaseException as error:report.update(status='FAIL',error=str(error));raise
    finally:
        stop.touch()
        if process and process.poll() is None:
            try:process.wait(timeout=30)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=10)
        (proof/'gui-deadline.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'status':report['status'],'report':str(proof/'gui-deadline.json')}))


if __name__=='__main__':main()
