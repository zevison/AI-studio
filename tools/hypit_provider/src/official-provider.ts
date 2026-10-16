/** Real Hypit 0.3.1 Endpoint. Network transport is deliberately absent until account verification. */
import { DatabaseSync } from 'node:sqlite';
import { createHash } from 'node:crypto';
import { canonicalize, defineEndpoint, wakeAfter } from '@hypit/hypit/endpoint';
import type { AsyncEndpoint, EndpointRequest, EndpointSupport, CanonicalValue } from '@hypit/hypit/endpoint';
import { sealGeneratedVideoSet } from '@hypit/generation';
import { seedanceEndpointsByModel } from '@hypit/seedance';
import { buildOpenLuxVideoPayload, requestFingerprint } from './openlux-contract.js';
import type { VideoSpec } from './openlux-contract.js';

const model = seedanceEndpointsByModel['seedance-2.5'];
export const providerName = '@seedance-studio/openlux-provider';
export type PollResult = { state: 'pending' | 'ready' | 'failed' };
/** Trusted backend port: approval must bind project, price, account and request before submit. */
export interface BackendPort {
  authorize(payload: Record<string, unknown>): Promise<void>;
  submit(payload: Record<string, unknown>, identity: string): Promise<string>;
  poll(id: string): Promise<PollResult>;
  collect(id: string): Promise<Uint8Array>;
}

type Row = { fingerprint: string; state: string; remote: string | null; created: number };
export class OperationLedger {
  constructor(private path: string) {
    this.withDb(db => db.exec(`CREATE TABLE IF NOT EXISTS operations (
      operation TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
      remote TEXT, created REAL NOT NULL)`));
  }
  private withDb<T>(fn: (db: DatabaseSync) => T): T {
    const db = new DatabaseSync(this.path);
    try {
      db.exec('PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA busy_timeout=5000;');
      return fn(db);
    } finally { db.close(); }
  }
  read(operation: string): Row | undefined {
    return this.withDb(db => db.prepare('SELECT fingerprint,state,remote,created FROM operations WHERE operation=?')
      .get(operation) as Row | undefined);
  }
  reserve(operation: string, fingerprint: string, now: number): void {
    // Commit BEFORE remote I/O. A crash at any later point must never imply permission to resubmit.
    this.withDb(db => db.prepare('INSERT INTO operations VALUES (?, ?, ?, NULL, ?)')
      .run(operation, fingerprint, 'submitting', now));
  }
  update(operation: string, state: string, remote: string | null): void {
    this.withDb(db => db.prepare('UPDATE operations SET state=?,remote=? WHERE operation=?').run(state, remote, operation));
  }
  close(): void { /* Every statement closes its connection, including errors. */ }
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid structured value');
  return value as Record<string, unknown>;
}
function id(value: unknown): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_.:-]{1,256}$/.test(value)) throw new Error('Invalid task identity');
  return value;
}
/** Only verified scalar mapping is currently supported; unsupported media is refused before I/O. */
export function officialPayload(request: EndpointRequest): Record<string, unknown> {
  if (request.capability.module.name !== model.capability.module.name ||
      request.capability.module.version !== model.capability.module.version ||
      request.capability.name !== model.capability.name ||
      request.returns.name !== model.returns.name ||
      request.returns.module.name !== model.returns.module.name ||
      request.returns.module.version !== model.returns.module.version) throw new Error('Exact model identity mismatch');
  if (request.pendingInputs?.length) throw new Error('Media/unfinished inputs require a verified account mapping');
  model.validateRequest(request.constraints);
  const ports = record(record(request.constraints).ports);
  const allowed = new Set(['prompt','duration','resolution','aspectRatio','generateAudio','webSearch']);
  if (Object.keys(ports).some(key => !allowed.has(key))) throw new Error('Media/edit/extend/motion mapping unverified');
  const first = (key: string) => (ports[key] as unknown[])[0];
  if (first('webSearch') !== false) throw new Error('OpenLux web search mapping unverified');
  const prompt = first('prompt');
  return buildOpenLuxVideoPayload({ prompt, taskType: 'text', references: [],
    duration: first('duration'), ratio: first('aspectRatio'), resolution: first('resolution'),
    generateAudio: first('generateAudio') } as VideoSpec);
}

export function createOfficialProvider(options: {
  instance: string; pool: string; account: string; ledger: OperationLedger; backend?: BackendPort;
  clock?: () => number; timeoutMs?: number; actionTimeoutMs?: number;
}) {
  const clock = options.clock ?? Date.now;
  const timeout = options.timeoutMs ?? 30 * 60_000;
  const actionTimeout = options.actionTimeoutMs ?? 120_000;
  if (!Number.isSafeInteger(timeout) || timeout < 1000 || timeout > 3600_000) throw new Error('Invalid operation timeout');
  if (!Number.isSafeInteger(actionTimeout) || actionTimeout < 1 || actionTimeout > 600_000) throw new Error('Invalid action timeout');
  async function bounded<T>(action: () => Promise<T>): Promise<T> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      return await Promise.race([action(), new Promise<never>((_resolve,reject) => {
        timer=setTimeout(() => reject(new Error('Backend action deadline exceeded')),actionTimeout);
      })]);
    } finally { if(timer) clearTimeout(timer); }
  }
  const support = (request: EndpointRequest): EndpointSupport => {
    try {
      officialPayload(request);
      if (!options.backend) return { status: 'unsupported', reason: 'OpenLux account protocol and spending authority unverified' };
      return { status: 'supported' };
    }
    catch { return { status: 'unsupported', reason: 'Exact OpenLux request/asset mapping is unsupported or unverified' }; }
  };
  const failure = (code: string) => ({ status: 'failed' as const, failure: { code, message: code } });
  const persisted = (operation: string, handle: CanonicalValue): Row => {
    const h = record(handle), row = options.ledger.read(operation);
    if (!row || !row.remote || row.remote !== h.id || row.fingerprint !== h.fingerprint)
      throw new Error('Operation handle does not match durable acknowledgement');
    return row;
  };
  const endpoint: AsyncEndpoint = {
    async start(context) {
      const payload = officialPayload(context.need);
      if (!options.backend) return failure('ACCOUNT_PROTOCOL_AND_SPEND_UNVERIFIED');
      const operation = id(context.operation);
      const fingerprint = requestFingerprint(options.account, payload);
      const existing = options.ledger.read(operation);
      if (existing) {
        if (existing.fingerprint !== fingerprint) return failure('OPERATION_REQUEST_CHANGED');
        if (!existing.remote) return failure('SUBMISSION_UNKNOWN_RECONCILE_REQUIRED');
        const handle = { id: existing.remote, fingerprint };
        await context.checkpoint?.({ handle, receipt: { id: existing.remote } });
        return { ...wakeAfter(handle, 1000, clock()), receipt: { id: existing.remote } };
      }
      await bounded(() => options.backend!.authorize(payload));
      try { options.ledger.reserve(operation, fingerprint, clock()); }
      catch { return failure('DUPLICATE_REQUEST_OR_STORAGE_CONFLICT'); }
      let remote: string;
      try { remote = id(await bounded(() => options.backend!.submit(payload, fingerprint))); }
      catch {
        options.ledger.update(operation, 'unknown', null);
        return failure('SUBMISSION_UNKNOWN_RECONCILE_REQUIRED');
      }
      options.ledger.update(operation, 'pending', remote);
      const handle = { id: remote, fingerprint };
      await context.checkpoint?.({ handle, receipt: { id: remote } });
      return { ...wakeAfter(handle, 1000, clock()), receipt: { id: remote } };
    },
    async poll(context) {
      const row = persisted(context.operation, context.handle);
      if (!options.backend) return failure('BACKEND_UNAVAILABLE');
      if (clock() - row.created >= timeout) return failure('LOCAL_TIMEOUT_REMOTE_MAY_CONTINUE');
      if (row.state === 'failed') return failure('REMOTE_TASK_FAILED');
      if (row.state === 'ready' || row.state === 'collected') return { status: 'ready', handle: context.handle };
      let response: PollResult;
      try { response = await bounded(() => options.backend!.poll(row.remote!)); }
      catch { return failure('POLL_FAILED_RECONCILE_REQUIRED'); }
      if (response.state === 'pending') return wakeAfter(context.handle, 1000, clock(), { phase: 'pending' });
      if (response.state === 'failed') {
        options.ledger.update(context.operation, 'failed', row.remote);
        return failure('REMOTE_TASK_FAILED');
      }
      if (response.state !== 'ready') return failure('UNKNOWN_REMOTE_STATE');
      options.ledger.update(context.operation, 'ready', row.remote);
      await context.checkpoint?.({ handle: context.handle, receipt: { id: row.remote! }, remoteEnded: true });
      return { status: 'ready', handle: context.handle };
    },
    async collect(context) {
      const row = persisted(context.operation, context.handle);
      if (!options.backend || !['ready','collected'].includes(row.state)) return failure('RESULT_NOT_READY');
      // Backend collector must run ffprobe and URL/DNS/hash/size validation before returning bytes.
      const bytes = await bounded(() => options.backend!.collect(row.remote!));
      if (!(bytes instanceof Uint8Array) || bytes.length < 12 || bytes.length > 400 * 1024 * 1024 ||
          Buffer.from(bytes.subarray(4,8)).toString('ascii') !== 'ftyp') return failure('INVALID_MP4_BYTES');
      const artifact = await context.resources.put(bytes, 'video/mp4');
      options.ledger.update(context.operation, 'collected', row.remote);
      await context.reportDiagnostic?.({ level: 'info', message: `Collected MP4 SHA256 ${createHash('sha256').update(bytes).digest('hex')}` });
      return { status: 'completed', result: { value: { kind: 'inline',
        value: canonicalize(sealGeneratedVideoSet({ videos: [artifact] })) } } };
    },
    // No verified remote cancel API; local interruption is never reported as remote cancellation.
    cancel: () => ({ status: 'unsupported' }),
  };
  return defineEndpoint({ instance: options.instance, pool: options.pool, defaultConcurrency: 10,
    actionLimits: { submit: { concurrency: 1 }, poll: { concurrency: 4 }, collect: { concurrency: 2 } },
    capabilities: [{ capability: model.capability, returns: model.returns,
      lifecycle: 'asynchronous', supports: support, endpoint }] });
}
