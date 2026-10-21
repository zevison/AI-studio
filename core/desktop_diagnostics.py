"""Bounded support export built from allowlisted facts, never copied user logs."""
from __future__ import annotations
import datetime
import importlib.metadata
import io
import json
import platform
import sqlite3
import sys
import zipfile
from . import config

PACKAGES=('pywebview','pythonnet','pyinstaller','curl-cffi','faster-whisper','tuf','cryptography')
STATES={'pending','running','completed','failed','cancelled','result_pending',
        'submission_unknown','interrupted','unknown','ready','blocked'}


def report():
    result={'format':'seedance.diagnostics@1','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'application':{'version':config.APP_VERSION,'build':config.APP_BUILD,'frozen':bool(getattr(sys,'frozen',False))},
        'system':{'os':platform.system(),'version':platform.version(),'architecture':platform.machine()},
        'python':platform.python_version(),'packages':{},'tasks':{},
        'privacy':{'raw_logs_included':False,'database_included':False,'credentials_included':False,
                   'prompts_and_media_included':False,'local_paths_included':False}}
    for package in PACKAGES:
        try: result['packages'][package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError: result['packages'][package]=None
    try:
        with sqlite3.connect(config.DB_PATH.resolve().as_uri()+'?mode=ro',uri=True,timeout=3) as connection:
            rows=connection.execute('SELECT status,COUNT(*) FROM tasks GROUP BY status').fetchall()
        for state,count in rows:
            key=state if state in STATES else 'other'
            result['tasks'][key]=result['tasks'].get(key,0)+count
    except (OSError,sqlite3.Error,ValueError): result['tasks_readable']=False
    try:
        from .configuration_service import snapshot
        document=snapshot()
        result['configuration']={'schema_version':document.get('schema_version'),
            'revision':document.get('revision'),'provider_count':len(document['providers']),
            'credential_reentry_required':bool(document.get('credential_errors'))}
    except (OSError,ValueError): result['configuration']={'readable':False}
    return result


def export_zip():
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('diagnostics.json',json.dumps(report(),ensure_ascii=False,indent=2))
        archive.writestr('README.txt','Privacy-safe diagnostic summary. Raw logs, databases, API keys, '
            'account identities, prompts, media and local paths are intentionally excluded. '
            'This report does not claim that external services or clean Windows installation passed.\n')
    return stream.getvalue()
