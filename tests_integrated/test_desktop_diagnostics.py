import io
import json
import sqlite3
import zipfile
from core import config,configuration_service,desktop_diagnostics as diagnostics


def test_diagnostics_allowlist_excludes_user_content_and_paths(tmp_path,monkeypatch):
    private='secret-account-token-DO-NOT-EXPORT'
    database=tmp_path/'private-user-name'/'studio.db';database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE tasks(status TEXT,prompt TEXT)')
        connection.executemany('INSERT INTO tasks VALUES(?,?)',[('completed',private),(private,private)])
    monkeypatch.setattr(config,'DB_PATH',database)
    monkeypatch.setattr(configuration_service,'snapshot',lambda:{'schema_version':1,'revision':7,
        'providers':[{'base_url':private,'api_key':private,'name':private}],
        'credential_errors':[private],'runtime':{'output_directory':str(database.parent)}})
    with zipfile.ZipFile(io.BytesIO(diagnostics.export_zip())) as archive:
        assert set(archive.namelist())=={'diagnostics.json','README.txt'}
        text=archive.read('diagnostics.json').decode('utf8')
        assert private not in text and str(database.parent) not in text
        report=json.loads(text)
        assert report['tasks']=={'completed':1,'other':1}
        assert report['configuration']['credential_reentry_required'] is True


def test_missing_or_corrupt_state_still_exports_summary(tmp_path,monkeypatch):
    database=tmp_path/'corrupt.db';database.write_bytes(b'corrupt')
    monkeypatch.setattr(config,'DB_PATH',database)
    def bad(): raise ValueError('private corrupted configuration')
    monkeypatch.setattr(configuration_service,'snapshot',bad)
    result=diagnostics.report()
    assert result['tasks_readable'] is False
    assert result['configuration']=={'readable':False}
    assert 'private corrupted' not in json.dumps(result)
