"""Backend-only bounded token authorization. Never accepts client-authored approval.

Reference-rate accounting is deliberately distinct from an actual vendor bill.
Uncertain submissions consume their full reservation and block further calls.
"""
from dataclasses import dataclass
from contextvars import ContextVar
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

@dataclass(frozen=True)
class TokenGrant:
    account: str
    model: str
    input_usd_per_million: str
    output_usd_per_million: str
    limit_usd: str
    max_calls: int
    max_output_tokens: int
    expires_at: float
    user_authorized: bool = False
    rates_verified: bool = False

class BoundedChat:
    def __init__(self, path: Path, grant: TokenGrant, sender, *, clock=time.time):
        self.path,self.grant,self.sender,self.clock=Path(path),grant,sender,clock
        self.owner = ContextVar('hypit_token_owner', default=None)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        amounts=[Decimal(grant.input_usd_per_million),Decimal(grant.output_usd_per_million),Decimal(grant.limit_usd)]
        if (not grant.user_authorized or not grant.rates_verified or not all(x.is_finite() and x>0 for x in amounts)
            or type(grant.max_calls) is not int or not 1<=grant.max_calls<=20
            or type(grant.max_output_tokens) is not int or not 1<=grant.max_output_tokens<=2048
            or not clock()<grant.expires_at<=clock()+3600):
            raise PermissionError('Verified rates and explicit bounded token authorization required')
        self.slice=amounts[2]/grant.max_calls
        policy=json.dumps([grant.account,grant.model,*map(str,amounts),grant.max_calls,grant.max_output_tokens],separators=(',',':'))
        self.policy=hashlib.sha256(policy.encode()).hexdigest()
        connection=self._connect()
        try:
            connection.executescript('CREATE TABLE IF NOT EXISTS grant_policy (id INTEGER PRIMARY KEY CHECK(id=1),fingerprint TEXT,expires REAL);'
                'CREATE TABLE IF NOT EXISTS token_calls(id TEXT PRIMARY KEY,state TEXT,request_hash TEXT,reserved_usd TEXT,'
                'prompt_tokens INTEGER,completion_tokens INTEGER,rate_cost_upper_usd TEXT,created REAL);')
            connection.execute('BEGIN IMMEDIATE')
            if 'owner' not in {r[1] for r in connection.execute('PRAGMA table_info(token_calls)')}:
                connection.execute('ALTER TABLE token_calls ADD COLUMN owner TEXT')
            if 'request_id' not in {r[1] for r in connection.execute('PRAGMA table_info(token_calls)')}:
                connection.execute('ALTER TABLE token_calls ADD COLUMN request_id TEXT')
            connection.execute('''CREATE TABLE IF NOT EXISTS token_billing_receipts(
                billing_id TEXT PRIMARY KEY, call_id TEXT UNIQUE, request_id TEXT,
                actual_usd TEXT, reconciled_at REAL)''')
            connection.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_token_billing_request ON token_billing_receipts(request_id)')
            connection.commit()
            row=connection.execute('SELECT fingerprint,expires FROM grant_policy WHERE id=1').fetchone()
            if row and row[0]!=self.policy: raise PermissionError('Existing grant cannot be replaced or expanded')
            if row: self.expires=row[1]
            else:
                self.expires=grant.expires_at
                connection.execute('INSERT INTO grant_policy VALUES(1,?,?)',(self.policy,self.expires))
                connection.commit()
        finally: connection.close()

    def _connect(self):
        connection=sqlite3.connect(self.path,timeout=5)
        connection.execute('PRAGMA synchronous=FULL')
        return connection

    def __call__(self,payload):
        grant=self.grant
        if payload.get('model')!=grant.model or not self.clock()<self.expires:
            raise PermissionError('Token model binding or grant expiry failed')
        wire=dict(payload)
        if 'max_tokens' in wire or 'max_completion_tokens' in wire:
            raise PermissionError('Caller cannot override the authorized output cap')
        wire.update(max_completion_tokens=grant.max_output_tokens,stream=False)
        raw=json.dumps(wire,ensure_ascii=False,separators=(',',':')).encode('utf8')
        if len(raw)>120000: raise PermissionError('Token input exceeds byte bound')
        input_bound=len(raw)+4096
        forecast=(Decimal(input_bound)*Decimal(grant.input_usd_per_million)+
            Decimal(grant.max_output_tokens)*Decimal(grant.output_usd_per_million))/Decimal(1_000_000)
        if forecast>self.slice: raise PermissionError('Conservative token forecast exceeds reserved slice')
        call=uuid.uuid4().hex
        connection=self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            count=connection.execute('SELECT COUNT(*) FROM token_calls').fetchone()[0]
            uncertain=connection.execute("SELECT 1 FROM token_calls WHERE state IN ('submission_unknown','usage_unverified','over_budget') LIMIT 1").fetchone()
            if count>=grant.max_calls or uncertain: raise PermissionError('Token grant exhausted or awaiting reconciliation')
            connection.execute('INSERT INTO token_calls(id,state,request_hash,reserved_usd,created,owner) VALUES(?,?,?,?,?,?)',
                (call,'submission_unknown',hashlib.sha256(raw).hexdigest(),str(self.slice),self.clock(),self.owner.get()))
            connection.commit()
        finally: connection.close()
        response=self.sender(wire)  # Exactly once. Exceptions leave durable quarantine.
        usage=response.get('usage',{}) if isinstance(response,dict) else {}
        prompt,completion=usage.get('prompt_tokens'),usage.get('completion_tokens')
        valid=(type(prompt) is int and prompt>=0 and type(completion) is int and 0<=completion<=grant.max_output_tokens)
        cost=(Decimal(prompt)*Decimal(grant.input_usd_per_million)+Decimal(completion)*Decimal(grant.output_usd_per_million))/Decimal(1_000_000) if valid else None
        state='acknowledged' if valid and cost<=self.slice else ('over_budget' if valid else 'usage_unverified')
        request_id = response.get('_request_id') if isinstance(response,dict) else None
        connection=self._connect()
        try:
            connection.execute('UPDATE token_calls SET state=?,prompt_tokens=?,completion_tokens=?,rate_cost_upper_usd=?,request_id=? WHERE id=?',
                (state,prompt if valid else None,completion if valid else None,str(cost) if cost is not None else None,
                 str(request_id)[:160] if request_id else None,call))
            connection.commit()
        finally: connection.close()
        if state!='acknowledged': raise PermissionError('Usage invalid or exceeds authorization; further token calls blocked')
        return response

    def evidence(self):
        connection=self._connect()
        try:
            connection.row_factory=sqlite3.Row
            return [dict(row) for row in connection.execute('SELECT * FROM token_calls ORDER BY created')]
        finally: connection.close()

    def reconcile_rejection(self, call_id, verified_log):
        """Trusted backend only: positive provider error evidence, never absence of a bill.

        The caller obtains this record via authenticated GET /api/log/self. The
        reservation and consumed call remain; only the quarantine is reconciled.
        """
        metadata=verified_log.get('billing_metadata') or {}
        if (verified_log.get('model_name')!=self.grant.model or verified_log.get('type')!=5
            or verified_log.get('quota')!=0 or verified_log.get('prompt_tokens')!=0
            or verified_log.get('completion_tokens')!=0 or metadata.get('error_code')!='invalid_request_error'
            or not isinstance(metadata.get('request_id'),str) or not metadata['request_id']):
            raise PermissionError('Provider rejection evidence is incomplete')
        connection=self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            row=connection.execute("SELECT created FROM token_calls WHERE id=? AND state='submission_unknown'",(call_id,)).fetchone()
            if not row or not row[0]<=verified_log.get('created_at',0)<=row[0]+180:
                raise PermissionError('Provider rejection does not match submission window')
            connection.execute("UPDATE token_calls SET state='rejected_confirmed',prompt_tokens=0,completion_tokens=0,rate_cost_upper_usd='0' WHERE id=?",(call_id,))
            connection.commit()
        finally:connection.close()

    def reconcile_settled_usage(self, call_id, verified_log, verified_status):
        """Trusted authenticated-log reconciliation, never absence-of-charge inference.

        Caller must establish a unique one-to-one match against the complete
        account log window. The used call and reservation remain consumed.
        """
        metadata = verified_log.get('billing_metadata') or {}
        prompt, completion = verified_log.get('prompt_tokens'), verified_log.get('completion_tokens')
        quota = verified_log.get('quota')
        if (verified_log.get('model_name') != self.grant.model or verified_log.get('type') != 2
            or type(prompt) is not int or prompt < 0 or type(completion) is not int
            or not 0 <= completion <= self.grant.max_output_tokens or type(quota) is not int or quota < 0
            or not isinstance(metadata.get('request_id'),str) or not metadata['request_id']
            or verified_status.get('currency_type') != 'USD' or not verified_log.get('id')):
            raise PermissionError('Positive settled billing evidence required')
        unit = Decimal(str(verified_status.get('quota_per_unit')))
        if not unit.is_finite() or unit <= 0:
            raise PermissionError('Billing unit not verified')
        actual = Decimal(quota) / unit
        rate_cost = (Decimal(prompt) * Decimal(self.grant.input_usd_per_million)
            + Decimal(completion) * Decimal(self.grant.output_usd_per_million)) / Decimal(1_000_000)
        if actual > self.slice or rate_cost > self.slice:
            raise PermissionError('Settled bill exceeds grant reservation')
        connection = self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute("SELECT created FROM token_calls WHERE id=? AND state='submission_unknown'", (call_id,)).fetchone()
            if not row or not row[0] <= verified_log.get('created_at',0) <= row[0] + 180:
                raise PermissionError('Settled billing record does not match the call window')
            connection.execute('INSERT INTO token_billing_receipts VALUES(?,?,?,?,?)',
                (str(verified_log['id']),call_id,metadata['request_id'],str(actual),self.clock()))
            connection.execute("UPDATE token_calls SET state='acknowledged',prompt_tokens=?,completion_tokens=?,rate_cost_upper_usd=? WHERE id=?",
                (prompt,completion,str(rate_cost),call_id))
            connection.commit()
        except Exception:
            connection.rollback();raise
        finally:connection.close()
