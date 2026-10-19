"""Short-lived loopback capability for the genuine Hypit Runtime adapter.

The API key never enters Node, project files or HTTP responses. A capability
can submit only the exact payload approved by the host, once. Restart does not
refund its durable grant, including uncertain submissions.
"""
from contextlib import contextmanager
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import traceback
from urllib.parse import urlsplit

from .hypit_openlux_gateway import request_sha256, DurableVideoGateway
from .hypit_provider_bridge import ProviderLifecycle, SpendEnvelope
from .hypit_agent_runtime import _atomic_json
from .hypit_output_policy import sha


class SubmissionBudget:
    def __init__(self, path, grant_id, account, *, limit_usd, max_submissions=1):
        self.path, self.grant_id, self.account = Path(path), grant_id, account
        self.limit = Decimal(str(limit_usd))
        if not self.limit.is_finite() or self.limit <= 0 or type(max_submissions) is not int or max_submissions < 1:
            raise ValueError('Invalid video budget')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS video_grants(
                id TEXT PRIMARY KEY, account TEXT NOT NULL, limit_usd TEXT NOT NULL, max_calls INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS video_reservations(
                grant_id TEXT NOT NULL, fingerprint TEXT NOT NULL, reserved_usd TEXT NOT NULL,
                created REAL NOT NULL, PRIMARY KEY(grant_id,fingerprint));''')
            c.execute('INSERT OR IGNORE INTO video_grants VALUES(?,?,?,?)',
                      (grant_id,account,str(self.limit),max_submissions))
            row = c.execute('SELECT account,limit_usd,max_calls FROM video_grants WHERE id=?',(grant_id,)).fetchone()
            if tuple(row) != (account,str(self.limit),max_submissions):
                raise PermissionError('Persisted video grant cannot be expanded or rebound')
        self.max_calls = max_submissions

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=20, isolation_level=None)
        try:
            connection.execute('PRAGMA synchronous=FULL')
            connection.execute('PRAGMA busy_timeout=20000')
            yield connection
        finally:
            connection.close()

    def reserve(self, payload, amount):
        amount = Decimal(str(amount))
        if not amount.is_finite() or not 0 < amount <= self.limit:
            raise PermissionError('Video reservation exceeds consent')
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            rows = c.execute('SELECT reserved_usd FROM video_reservations WHERE grant_id=?',(self.grant_id,)).fetchall()
            if len(rows) >= self.max_calls or sum((Decimal(x[0]) for x in rows),Decimal(0)) + amount > self.limit:
                raise PermissionError('Video grant is exhausted; uncertain calls remain reserved')
            c.execute('INSERT INTO video_reservations VALUES(?,?,?,?)',
                      (self.grant_id,request_sha256(payload),str(amount),time.time()))
            c.execute('COMMIT')


class RuntimeBackend:
    def __init__(self, lifecycle: ProviderLifecycle, payload, quote, budget: SubmissionBudget,
                 directory, *, reserved_usd, approved_by_user=False, output_policy=None, reference_manifest=None,on_ack=None):
        self.lifecycle, self.payload, self.quote, self.budget = lifecycle,payload,quote,budget
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.reserved = Decimal(str(reserved_usd))
        if (approved_by_user is not True or quote['account'] != lifecycle.account
            or quote['account'] != budget.account or quote['currency'] != 'USD'
            or quote['request_hash'] != request_sha256(payload)
            or not Decimal(quote['estimated_usd']) <= self.reserved <= budget.limit
            or not 0 <= time.time()-quote['checked_at'] < 300):
            raise PermissionError('Fresh exact quote, account and explicit consent required')
        self.token = secrets.token_urlsafe(48)
        self.expires = time.time()+3500
        self.lock = threading.RLock()
        self.server = self.thread = None
        self.allow_submission = True
        self.output_policy = output_policy
        self.on_ack=on_ack
        self.references = {}
        for ref in reference_manifest or []:
            key=(ref['sha256'],ref['role'],ref.get('personReference'))
            if key in self.references and self.references[key]!=ref['url']:
                raise ValueError('Ambiguous approved media reference')
            self.references[key]=ref['url']

    def authorize(self, payload):
        if request_sha256(payload) != self.quote['request_hash'] or time.time() >= self.expires:
            raise PermissionError('Runtime capability payload changed or expired')

    def submit(self, payload):
        self.authorize(payload)
        if not self.allow_submission:
            raise PermissionError('Recovery capability cannot create remote tasks')
        with self.lock:
            self.budget.reserve(payload,self.reserved)
            spend = SpendEnvelope(self.lifecycle.account,self.quote['request_hash'],
                self.quote['estimated_usd'],str(self.reserved),self.expires-3500,self.expires,True,True)
            remote=self.lifecycle.start(payload,spend)['task_id']
            if self.on_ack:self.on_ack(remote)
            return remote

    def assert_task(self, remote):
        row = self.lifecycle.ledger.info(self.payload)
        if not row or not row['task_id'] or row['task_id'] != remote:
            raise PermissionError('Remote task is not owned by this exact Runtime capability')

    def reference(self,request):
        self.authorize(self.payload)
        if set(request)-{'sha256','role','personReference'}:raise PermissionError('Unknown reference fields')
        key=(request.get('sha256'),request.get('role'),request.get('personReference'))
        if key not in self.references:raise PermissionError('Media hash, role or classification changed')
        return {'url':self.references[key]}

    def collect(self, remote):
        self.assert_task(remote)
        with self.lock:
            # Keep leaf names short enough for Windows' temporary-file suffixes.
            # The receipt still binds the full hash and exact remote task; a
            # prefix collision is rejected rather than reused or overwritten.
            destination = self.directory / (request_sha256(self.payload)[:24]+'.mp4')
            receipt=destination.with_suffix('.receipt.json')
            if destination.exists():
                if receipt.is_symlink() or not receipt.is_file() or receipt.stat().st_size>16384:
                    raise PermissionError('Collected artifact has no verifiable receipt; preserve and reconcile it')
                evidence=json.loads(receipt.read_text(encoding='utf8'))
                if (destination.is_symlink() or not destination.is_file() or
                    evidence.get('task_id')!=remote or evidence.get('request_hash')!=request_sha256(self.payload)
                    or evidence.get('sha256')!=sha(destination)):
                    raise PermissionError('Cached collected artifact changed; no unverified bytes returned')
            else:
                self.lifecycle.collect(self.payload,destination)
            if destination.is_symlink() or not 0<destination.stat().st_size <= 400*1024*1024:
                raise PermissionError('Invalid collected artifact')
            if self.output_policy:self.output_policy(destination,self.payload)
            _atomic_json(receipt,{'task_id':remote,'request_hash':request_sha256(self.payload),
                'sha256':sha(destination),'size':destination.stat().st_size})
            return destination.read_bytes()

    def __enter__(self):
        owner = self
        def failure(action,exc):
            try:
                frames=traceback.extract_tb(exc.__traceback__)[-8:]
                _atomic_json(owner.directory/'last-action-error.json',{'action':action if action in {'/collect','/poll','/submit','/reference','/authorize'} else 'unknown',
                    'exception':type(exc).__name__,'time':time.time(),
                    'frames':[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno} for f in frames]})
            except OSError:pass
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self,*args): pass  # no capability or signed URL in logs
            def do_POST(self):
                self.connection.settimeout(10)
                data, status, mime = b'',200,'application/json'
                try:
                    if (self.client_address[0] != '127.0.0.1'
                        or self.headers.get('Host') != f'127.0.0.1:{owner.server.server_port}'
                        or self.headers.get('Origin') is not None
                        or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+owner.token)
                        or self.headers.get('Content-Type') != 'application/json'):
                        raise PermissionError('Runtime capability required')
                    length = int(self.headers.get('Content-Length','0'))
                    if not 1 <= length <= 100_000 or self.headers.get('Transfer-Encoding'):
                        raise ValueError('Invalid request size')
                    raw = self.rfile.read(length)
                    if len(raw) != length: raise ValueError('Incomplete request')
                    request = json.loads(raw)
                    if not isinstance(request,dict):raise ValueError('Object required')
                    if self.path == '/authorize':
                        owner.authorize(request['payload']);result = {'ok':True}
                    elif self.path == '/submit':
                        result = {'id':owner.submit(request['payload'])}
                    elif self.path == '/poll':
                        owner.assert_task(request['id'])
                        result = {'state':owner.lifecycle.poll(owner.payload)['phase']}
                    elif self.path == '/collect':
                        data = owner.collect(request['id']);mime='video/mp4';result=None
                    elif self.path == '/reference':
                        result = owner.reference(request)
                    else:raise ValueError('Unknown Runtime action')
                    if result is not None:data=json.dumps(result).encode()
                except PermissionError as exc:
                    failure(self.path,exc)
                    status,data=403,b'{"error":"Runtime authorization rejected"}'
                except Exception as exc:
                    failure(self.path,exc)
                    status,data=409,b'{"error":"Runtime action failed; inspect durable task ledger; do not resubmit"}'
                self.send_response(status)
                self.send_header('Content-Type',mime)
                self.send_header('Content-Length',str(len(data)))
                self.send_header('Connection','close')
                self.end_headers()
                self.wfile.write(data)
                self.close_connection=True
        self.server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        return self

    def config(self):
        if not self.server:raise RuntimeError('Backend is not running')
        return {'account':self.lifecycle.account,'bridgeUrl':f'http://127.0.0.1:{self.server.server_port}',
                'bridgeToken':self.token}

    def __exit__(self,*args):
        if self.server:
            self.server.shutdown();self.server.server_close()
            self.thread.join(timeout=3)
        self.server=self.thread=None
