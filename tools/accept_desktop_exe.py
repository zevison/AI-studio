"""Exercise the actual frozen program in a fresh, explicit test profile.

Only local HTTP APIs and free Hypit HTML rendering are used. This is a developer
Windows executable test, never evidence of clean Windows installation/reboot.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def clean_environment():
    # Do not inherit account keys, selected production homes or Python hooks.
    result = {k: v for k, v in os.environ.items() if not any(
        marker in k.upper() for marker in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD'))
        and not k.upper().startswith(('STUDIO_', 'PYTHON', 'OPENLUX_', 'OSS_', 'BRIGHTDATA_'))}
    result['OPEN_BROWSER'] = '0'
    return result


class Application:
    def __init__(self, exe, home, evidence, name):
        self.exe, self.home, self.evidence = Path(exe), Path(home), Path(evidence)
        self.name = name
        self.ready = self.evidence / (name + '-ready.json')
        self.shutdown = self.evidence / (name + '-stop')
        self.log = None
        self.process = None
        self.info = None
        self.token = None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def start(self):
        self.log = (self.evidence / (self.name + '-process.log')).open('w', encoding='utf8')
        command = [str(self.exe), '--headless', '--data-home', str(self.home),
                   '--ready-file', str(self.ready), '--shutdown-file', str(self.shutdown)]
        self.process = subprocess.Popen(command, cwd=self.evidence, env=clean_environment(),
            stdout=self.log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f'{self.name}: actual EXE exited {self.process.returncode}; inspect process/home logs')
            if self.ready.is_file():
                self.info = json.loads(self.ready.read_text(encoding='utf8'))
                health = self.request('/api/bootstrap-health')
                if health['pid'] != self.info['pid'] or Path(health['data_dir']).resolve() != (self.home / 'data').resolve():
                    raise AssertionError('Readiness did not identify this isolated test process')
                self.token = self.request('/api/hypit/session')['token']
                return self
            time.sleep(.2)
        raise TimeoutError('Actual executable readiness exceeded 180 seconds')

    def request(self, route, payload=None, *, token=True, origin=None, raw=False):
        if not route.startswith('/') or route.startswith('//'):
            raise ValueError('Local application route required')
        headers = {'Content-Type': 'application/json'}
        if token and self.token:
            headers['X-Hypit-Session'] = self.token
        if origin:
            headers['Origin'] = origin
        request = urllib.request.Request(self.info['url'] + route,
            data=None if payload is None else json.dumps(payload).encode('utf8'), headers=headers)
        with self.opener.open(request, timeout=180) as response:
            body = response.read()
        if raw:
            return body
        doc = json.loads(body)
        if not doc.get('ok'):
            raise AssertionError(doc)
        return doc['data']

    def upload(self, file):
        boundary = 'SeedanceAcceptance' + uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="synthetic.mp4"\r\n'
                'Content-Type: video/mp4\r\n\r\n').encode() + Path(file).read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
        request = urllib.request.Request(self.info['url'] + '/api/upload', data=body,
            headers={'Content-Type': 'multipart/form-data; boundary=' + boundary, 'X-Hypit-Session': self.token})
        with self.opener.open(request, timeout=60) as response:
            doc = json.load(response)
        assert doc.get('ok'), doc
        return doc['data'][0]

    def close(self):
        try:
            if self.process and self.process.poll() is None:
                self.shutdown.touch()
                try:
                    self.process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    # Target only a PID created by this harness. Never find/kill
                    # processes by executable name or port.
                    if os.name == 'nt':
                        subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/taskkill.exe'),
                            '/PID', str(self.process.pid), '/T', '/F'], capture_output=True, timeout=15)
                    else:
                        self.process.terminate()
                    self.process.wait(timeout=10)
                    raise RuntimeError('Graceful desktop shutdown failed; owned process forcibly stopped')
                if self.process.returncode:
                    raise RuntimeError(f'Actual executable exited {self.process.returncode} at shutdown')
        finally:
            if self.log:
                self.log.close()


def expect_http_error(action, status):
    try:
        action()
    except urllib.error.HTTPError as exc:
        assert exc.code == status, (exc.code, exc.read().decode('utf8', 'replace'))
    else:
        raise AssertionError('Forbidden request was accepted')


def command(command, evidence, label, timeout=30):
    result = subprocess.run(list(map(str, command)), capture_output=True, timeout=timeout,
        env=clean_environment(), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    (evidence / (label + '.log')).write_bytes(result.stdout + b'\nSTDERR:\n' + result.stderr)
    if result.returncode:
        raise AssertionError(f'{label} returned {result.returncode}')
    return result.stdout.decode('utf8', 'replace')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', required=True, type=Path)
    parser.add_argument('--evidence', required=True, type=Path)
    parser.add_argument('--fixtures', type=Path, default=ROOT / 'tools/acceptance/fixtures/projects/hp_free')
    args = parser.parse_args(argv)
    exe = args.exe.resolve(strict=True)
    evidence = args.evidence.resolve()
    if evidence.exists():
        raise FileExistsError('Use a new evidence directory; existing user files are never replaced')
    evidence.mkdir(parents=True)
    home = evidence / '中文 空格 用户目录'
    report = {'format': 'seedance.desktop-acceptance@1', 'executable': str(exe), 'exe_sha256': digest(exe),
        'paid_calls': 0, 'scope': 'Actual packaged process on existing Windows host; no clean OS claim',
        'clean_windows_install': 'NOT TESTED', 'os_reboot': 'NOT TESTED', 'gui_webview2': 'NOT TESTED', 'checks': []}
    active = []
    def record(label, function):
        started = time.monotonic()
        try:
            details = function()
            report['checks'].append({'name': label, 'status': 'PASS', 'seconds': round(time.monotonic()-started, 3), 'details': details})
            return details
        except Exception as exc:
            report['checks'].append({'name': label, 'status': 'FAIL', 'error': str(exc), 'type': type(exc).__name__})
            raise
    def start(name, selected_home=home):
        app = Application(exe, selected_home, evidence, name)
        active.append(app)
        app.start()
        return app
    try:
        app = record('no-key startup and isolated Chinese spaced data path', lambda: start('initial'))
        # Application objects are not serializable; retain only actual readiness facts.
        report['checks'][-1]['details'] = app.info
        def resources():
            html = app.request('/', raw=True)
            assert b'Seedance' in html
            for route in ('/static/configuration.js', '/static/chat_workspace.js', '/static/style.css'):
                assert app.request(route, raw=True)
            return {'html_bytes': len(html), 'static_resources': 3}
        record('real local page and static resources', resources)
        def components_ready():
            deadline=time.monotonic()+1800
            initial=app.request('/api/components/status')
            assert not initial['active'],'Fresh application downloaded a plugin without consent'
            approved=set()
            while time.monotonic()<deadline:
                state=app.request('/api/components/status')
                (evidence/'components.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf8')
                failed=[row for row in state['components'] if row.get('state') in {'failed','install_failed','needs_repair','paused'}]
                assert not failed,failed
                if all(row.get('state')=='ready' for row in state['components']):return state
                if not state['active']:
                    needed=next((row for row in state['components'] if row.get('state')!='ready' and row['id'] not in approved),None)
                    if needed:
                        app.request('/api/components/download',{'id':needed['id'],'confirmed':True})
                        approved.add(needed['id'])
                time.sleep(1)
            raise TimeoutError('Background components did not finish within the bounded deadline')
        record('pinned background components (or fully bundled build)',components_ready)
        def health_check():
            health = app.request('/api/hypit/health')
            assert health['available'] and health['local_build'] and health['skill_installed'], health
            system = app.request('/api/system/health')
            report['system'] = system
            assert system['required_ok'], system
            return health
        record('official Hypit installation and system health', health_check)
        root = Path(app.request('/api/bootstrap-health')['root_dir'])
        ffmpeg, ffprobe = root/'tools/ffmpeg/bin/ffmpeg.exe', root/'tools/ffmpeg/bin/ffprobe.exe'
        record('bundled FFmpeg process', lambda: command([ffmpeg, '-version'], evidence, 'ffmpeg'))
        record('bundled Node process', lambda: command([root/'tools/node/node.exe', '--version'], evidence, 'node'))
        if (root/'tools/components-profile.json').is_file():
            from core.component_manager import catalog,location
            item=catalog(root)['browser']
            chromium=location(home,'browser',item)/item['cache_root']/'chrome-headless-shell-win64/chrome-headless-shell.exe'
        else:chromium = next((home/'assets/hypit-host').rglob('chrome-headless-shell.exe'))
        record('bundled Chromium process', lambda: command([chromium, '--version'], evidence, 'chromium'))
        def downloader_worker():
            version=command([exe, '--worker-module', 'yt_dlp', '--version'], evidence, 'yt-dlp')
            assert version.strip() and any(character.isdigit() for character in version), 'Frozen downloader did not emit its version to the inherited pipe'
            return version
        record('frozen yt-dlp module dispatcher',downloader_worker)
        def reject_untrusted():
            expect_http_error(lambda: app.request('/api/settings', {'http_timeout': 91}, token=False), 403)
            expect_http_error(lambda: app.request('/api/settings', {'http_timeout': 91}, origin='https://attacker.invalid'), 403)
            return {'missing_session': 403, 'cross_origin': 403}
        record('settings session and cross-origin protection', reject_untrusted)
        def config_roundtrip():
            doc = app.request('/api/configuration')
            revision = doc['revision']
            custom = copy.deepcopy(doc['providers'][0])
            custom.update(id='acceptance-custom', name='本地验收未认证账号', adapter='openai_chat', legacy_credential=False)
            custom['models']['chat'] = 'acceptance-custom-model'
            custom['parameters']['chat'] = {'temperature': .25, 'max_tokens': 1024}
            doc['providers'].append(custom)
            doc['defaults']['chat'] = custom['id']
            doc['runtime']['http_timeout'] = 91
            saved = app.request('/api/configuration', {'document': doc, 'expected_revision': revision})
            assert saved['revision'] == revision+1
            assert saved['defaults']['chat'] == custom['id']
            assert app.request('/api/settings')['http_timeout'] == 91
            # Exercise edit, disabled-account persistence and deletion without
            # changing the selected profile or contacting a service provider.
            edited=copy.deepcopy(saved)
            selected=next(row for row in edited['providers'] if row['id']==custom['id'])
            selected['name']='已编辑的本地验收账号'
            disabled=copy.deepcopy(selected)
            disabled.update(id='acceptance-disabled',enabled=False)
            edited['providers'].append(disabled)
            edited=app.request('/api/configuration',{'document':edited,'expected_revision':saved['revision']})
            assert any(row['id']=='acceptance-disabled' and row['enabled'] is False for row in edited['providers'])
            assert next(row for row in edited['providers'] if row['id']==custom['id'])['name']=='已编辑的本地验收账号'
            edited['providers']=[row for row in edited['providers'] if row['id']!='acceptance-disabled']
            saved=app.request('/api/configuration',{'document':edited,'expected_revision':edited['revision']})
            assert all(row['id']!='acceptance-disabled' for row in saved['providers'])
            exported = app.request('/api/configuration/export')
            assert 'api_key' not in json.dumps(exported)
            return {'revision': saved['revision'], 'model': custom['models']['chat'], 'real_request_performed': False}
        record('configuration create custom provider, selection, runtime propagation and export', config_roundtrip)
        def duplicate():
            second = Application(exe, home, evidence, 'duplicate')
            active.append(second)
            second.log = (evidence/'duplicate-process.log').open('w',encoding='utf8')
            second.process = subprocess.Popen([str(exe),'--headless','--data-home',str(home),'--ready-file',str(second.ready),'--shutdown-file',str(second.shutdown)],
                env=clean_environment(), stdout=second.log, stderr=subprocess.STDOUT)
            second.process.wait(timeout=20)
            assert second.process.returncode != 0 and not second.ready.exists()
            return {'rejected_exit_code': second.process.returncode}
        record('same-profile second instance refused', duplicate)
        def free_film():
            asset = app.upload(args.fixtures/'assets/material-1.mp4')
            project = app.request('/api/hypit/create', {'objective':'Offline actual EXE synthetic Film acceptance', 'title':'免费安装验收', 'asset_ids':[asset['id']]})
            folder = home/'data/hypit_projects'/project['id']
            shutil.copy2(args.fixtures/'main.svml', folder/'main.svml')
            shutil.copy2(args.fixtures/'look.svs', folder/'look.svs')
            validation = app.request('/api/hypit/validate', {'id':project['id']})
            assert validation['ok'], validation
            app.request('/api/hypit/build', {'id':project['id']})
            deadline = time.monotonic()+180
            while time.monotonic()<deadline:
                result = app.request('/api/hypit/project?id='+project['id'])
                if result['status'] not in {'building','exporting','validating'}:
                    break
                time.sleep(.4)
            assert result['status']=='completed', result
            output=result['metadata']['result']
            assert .95<=output['media']['duration']<=1.1
            # Resolve only actual export fields published by the service.
            output_root=Path(app.request('/api/status')['output_dir'])
            exports=list(output_root.glob('*.mp4'))
            assert len(exports)==1
            probe=json.loads(command([ffprobe,'-v','error','-show_streams','-show_format','-of','json',exports[0]],evidence,'film-ffprobe'))
            assert any(stream['codec_type']=='video' for stream in probe['streams'])
            return {'project_id':project['id'],'asset_id':asset['id'],'output':str(exports[0]),'sha256':digest(exports[0]),'result':output}
        film = record('HTTP upload, material binding, official free HTML Runtime, export and actual FFprobe',free_film)
        changed_output=evidence/'自定义 成片 输出'
        def change_output_directory():
            document=app.request('/api/configuration')
            document['runtime']['output_directory']=str(changed_output)
            saved=app.request('/api/configuration',{'document':document,'expected_revision':document['revision']})
            assert saved['runtime']['output_directory']==str(changed_output)
            return {'configured':str(changed_output),'restart_required':True}
        record('save new output directory without moving historical media',change_output_directory)
        record('graceful actual EXE shutdown',lambda:app.close())
        app = start('restart')
        def restored():
            config=app.request('/api/configuration')
            assert config['defaults']['chat']=='acceptance-custom'
            assert app.request('/api/settings')['http_timeout']==91
            project=app.request('/api/hypit/project?id='+film['project_id'])
            assert project['status']=='completed'
            assert digest(Path(film['output']))==film['sha256']
            assert Path(app.request('/api/status')['output_dir']).resolve()==changed_output.resolve()
            original_bytes=app.request('/outputs/'+Path(film['output']).name,raw=True)
            assert hashlib.sha256(original_bytes).hexdigest()==film['sha256']
            return {'settings_persisted':True,'project_completed':True,'output_hash_unchanged':True,
                    'changed_output_directory_active':True,'historical_output_http_hash_preserved':True}
        record('configuration, project and export persistence after EXE restart', restored)
        second_film=record('new Film exported to configured output directory',free_film)
        assert Path(second_film['output']).parent.resolve()==changed_output.resolve()
        record('restart graceful shutdown',lambda:app.close())
        for name, filename, bytes_value in (('corrupt-config','configuration.json',b'{broken'),('corrupt-vault','secrets.dpapi',b'not valid dpapi')):
            broken_home=evidence/(name+' 用户目录');broken_home.mkdir()
            file=broken_home/filename;file.write_bytes(bytes_value)
            broken=start(name,broken_home)
            def survives():
                configuration=broken.request('/api/configuration')
                assert configuration.get('error') or configuration.get('credential_error')
                assert file.read_bytes()==bytes_value
                return {'startup':True,'error_visible':True,'original_preserved':True}
            record(name+' startup and retained evidence',survives)
            record(name+' shutdown',lambda:broken.close())
    except Exception as exc:
        report['status']='FAIL'
        report['failure']={'type':type(exc).__name__,'error':str(exc)}
    finally:
        for app in reversed(active):
            try:
                app.close()
            except Exception as exc:
                report['checks'].append({'name':'owned-process cleanup','status':'FAIL','error':str(exc)})
        report.setdefault('status','PASS')
        if any(check['status']=='FAIL' for check in report['checks']):report['status']='FAIL'
        (evidence/'desktop-acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'status':report['status'],'report':str(evidence/'desktop-acceptance.json'),'paid_calls':0}))
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
