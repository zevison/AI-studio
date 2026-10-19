/**
 * Seedance Studio Hypit ↔ OpenLux wire contract (development only).
 * 【注意/Risk】No OpenLux account-specific production video API contract has been verified.
 * All remote submissions must remain disabled until an explicit, separately reviewed release gate.
 * This module does not store credentials or perform network requests.
 */
import { createHash } from 'node:crypto';
import { isIP } from 'node:net';

export class ContractViolation extends Error { constructor(message: string) { super(message); this.name = 'ContractViolation'; } }
export class SubmissionAmbiguous extends Error { constructor() { super('Remote submission may have been accepted; reconciliation required, no retry'); this.name = 'SubmissionAmbiguous'; } }

export type ReferenceRole = 'reference_image' | 'reference_video' | 'reference_audio' | 'first_frame' | 'last_frame';
export type Reference = { role: ReferenceRole; url: string; personReference?: boolean };
export type VideoSpec = {
  prompt: string; taskType: 'text' | 'reference' | 'edit' | 'extend';
  references: Reference[]; duration: number; ratio: string; resolution: '480p' | '720p' | '1080p';
  generateAudio: boolean;
};

const model = 'doubao-seedance-2-5-260628';
const counts = { reference_image: 30, reference_video: 10, reference_audio: 10, first_frame: 1, last_frame: 1 } as const;
const acceptedRatios = new Set(['adaptive', '16:9', '9:16', '1:1', '4:3', '3:4', '21:9']);
const states: Readonly<Record<string, 'pending'|'ready'|'failed'>> = {
  queued:'pending', pending:'pending', submitted:'pending', running:'pending', processing:'pending', in_progress:'pending',
  succeeded:'ready', success:'ready', completed:'ready', complete:'ready',
  failed:'failed', error:'failed', expired:'failed', cancelled:'failed', canceled:'failed'
};

function object(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new ContractViolation(`${label}: expected object`);
  return value as Record<string, unknown>;
}
function nonempty(value: unknown, label: string, max = 4096): string {
  if (typeof value !== 'string' || value.length === 0 || value.length > max) throw new ContractViolation(`${label}: invalid text`);
  return value;
}
export function externalHttps(value: unknown): string {
  const address = nonempty(value, 'URL');
  let uri: URL;
  try { uri = new URL(address); } catch { throw new ContractViolation('Invalid URL'); }
  if (uri.protocol !== 'https:' || uri.username || uri.password || uri.hash || uri.port && uri.port !== '443')
    throw new ContractViolation('Only authenticated-free HTTPS resource URLs on default port are allowed');
  const host = uri.hostname.toLowerCase().replace(/^\[|\]$/g, '');
  const ipVersion = isIP(host);
  const privateIpv4 = ipVersion === 4 && (() => {
    const octets = host.split('.').map(Number);
    return octets[0] === 0 || octets[0] === 10 || octets[0] === 127 || octets[0] === 169 && octets[1] === 254 ||
      octets[0] === 172 && octets[1]! >= 16 && octets[1]! <= 31 ||
      octets[0] === 192 && octets[1] === 168 || octets[0]! >= 224;
  })();
  if (!host.includes('.') || host.endsWith('.local') || host.endsWith('.localhost') || host === 'localhost' ||
      host === 'metadata.google.internal' || privateIpv4 || ipVersion === 6) {
    throw new ContractViolation('Local/private target is forbidden');
  }
  // URL validation is only preliminary; downloads must separately resolve DNS, block redirects,
  // pin public IPs, constrain bytes and check media signatures (not implemented here).
  return address;
}

/**
 * Preserve all reference roles; a required personReference value is not silently dropped.
 * Visual person classification has no VERIFIED OpenLux wire mapping yet, so reject any
 * affected operation before it can become a paid request.
 */
export function buildOpenLuxVideoPayload(spec: VideoSpec, verifiedVisualMapping = false): Record<string, unknown> {
  if (!spec || typeof spec !== 'object') throw new ContractViolation('Missing video spec');
  if (typeof spec.prompt !== 'string' || !spec.prompt.trim() || spec.prompt.length > 10000) throw new ContractViolation('Invalid prompt');
  if (!['text', 'reference', 'edit', 'extend'].includes(spec.taskType)) throw new ContractViolation('Unknown task type');
  if (!['480p', '720p', '1080p'].includes(spec.resolution) || typeof spec.generateAudio !== 'boolean') throw new ContractViolation('Invalid model settings');
  if (!Number.isInteger(spec.duration) || !acceptedRatios.has(spec.ratio)) throw new ContractViolation('Invalid time or ratio');
  if (!Array.isArray(spec.references) || spec.references.length > 50) throw new ContractViolation('Invalid references');
  const buckets: Record<ReferenceRole, number> = {reference_image:0,reference_video:0,reference_audio:0,first_frame:0,last_frame:0};
  const content: Array<Record<string, unknown>> = [{type:'text',text:spec.prompt.trim()}];
  for (const reference of spec.references) {
    if (!reference || typeof reference !== 'object' || !Object.prototype.hasOwnProperty.call(buckets, reference.role))
      throw new ContractViolation('Unknown reference role');
    const unexpected = Object.keys(reference).filter(key => !['role','url','personReference'].includes(key));
    if (unexpected.length) throw new ContractViolation('Unknown media field cannot be discarded');
    if (++buckets[reference.role] > counts[reference.role]) throw new ContractViolation('Too many references of one role');
    const url = externalHttps(reference.url);
    if (reference.role !== 'reference_audio') {
      if (typeof reference.personReference !== 'boolean') throw new ContractViolation('Visual reference person classification required by Hypit');
      // This metadata cannot be passed in a confirmed OpenLux wire field yet.
      if (!verifiedVisualMapping || reference.personReference)
        throw new ContractViolation('OpenLux person-reference transport/permission unverified; paid submission disabled');
      // false is a local classification/authorization guard. The documented
      // vendor API transports the media role and URL, not a made-up flag.
      const type=reference.role==='reference_video'?'video_url':'image_url';
      content.push({type,[type]:{url},role:reference.role});
      continue;
    }
    if (reference.personReference !== undefined) throw new ContractViolation('Audio must not have personReference');
    content.push({type:'audio_url', audio_url:{url},role:reference.role});
  }
  if (spec.taskType === 'text') {
    if (spec.references.length !== 0) throw new ContractViolation('Text task with media');
    if (spec.duration < 4 || spec.duration > 30 || spec.ratio === 'adaptive') throw new ContractViolation('Text task duration/ratio');
  } else if (spec.taskType === 'reference') {
    if (!spec.references.length || spec.duration !== -1 && (spec.duration < 4 || spec.duration > 30)) throw new ContractViolation('Invalid reference generation');
  } else {
    if (buckets.reference_video !== 1 || spec.ratio !== 'adaptive' ||
      spec.taskType==='edit' && spec.duration!==-1 ||
      spec.taskType==='extend' && spec.duration!==-1 && (spec.duration<4 || spec.duration>30))
      throw new ContractViolation('Edit/extend settings differ from the official 2.5 contract');
  }
  const payload: Record<string, unknown> = {model, content, ratio:spec.ratio, resolution:spec.resolution,
    duration:spec.duration, generate_audio:spec.generateAudio,output_format:'mp4'};
  if (spec.taskType !== 'text') payload.omni_reference_task_type = spec.taskType;
  return payload;
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>)
      .sort(([a],[b]) => a.localeCompare(b)).map(([k,v]) => [k,canonical(v)]));
  }
  if (typeof value === 'number' && !Number.isFinite(value)) throw new ContractViolation('Non-finite numeric field');
  return value;
}
export function requestFingerprint(account: string, payload: Record<string, unknown>): string {
  nonempty(account, 'Account identity', 256);
  return createHash('sha256').update(JSON.stringify(canonical({account, payload}))).digest('hex');
}
function nested(response: unknown): Record<string, unknown>[] {
  const top = object(response, 'Response');
  return typeof top.data === 'object' && top.data !== null && !Array.isArray(top.data)
    ? [top, top.data as Record<string, unknown>] : [top];
}
export function remoteTaskId(response: unknown): string {
  const values = nested(response).flatMap(part => ['id','task_id'].filter(k=>part[k]!==undefined).map(k=>String(part[k])));
  if (values.length === 0 || new Set(values).size !== 1 || !/^[A-Za-z0-9_.:-]{1,256}$/.test(values[0]!))
    throw new ContractViolation('Missing, conflicting or invalid remote task identity');
  return values[0]!;
}
export function remoteState(response: unknown): 'pending'|'ready'|'failed' {
  const values = nested(response).flatMap(part => ['status','task_status','state'].filter(k=>part[k]!==undefined).map(k=>String(part[k]).toLowerCase()));
  if (values.length === 0 || new Set(values).size !== 1 || !Object.prototype.hasOwnProperty.call(states, values[0]!))
    throw new ContractViolation('Unknown or inconsistent remote state');
  return states[values[0]!]!;
}
export function finalVideoUrl(response: unknown): string {
  const root = object(response,'Response');
  const candidates: unknown[] = [];
  for (const part of [root,root.data,root.output]) {
    if (typeof part !== 'object' || !part || Array.isArray(part)) continue;
    const node = part as Record<string,unknown>;
    for (const key of ['video_url','output_url']) if (node[key]!==undefined) candidates.push(node[key]);
    if (typeof node.output === 'string') candidates.push(node.output);
    if (node.content && typeof node.content === 'object' && !Array.isArray(node.content)) {
      const content = node.content as Record<string, unknown>;
      for (const key of ['video_url','output_url']) if (content[key] !== undefined) candidates.push(content[key]);
    }
  }
  const unique = [...new Set(candidates.map(x=>String(x)))];
  if (unique.length !== 1) throw new ContractViolation('No unique, validated completed video URL');
  return externalHttps(unique[0]);
}
export function assertPollIdentity(response: unknown, expectedId: string): void {
  nonempty(expectedId,'Task ID',256);
  let found = false;
  for (const part of nested(response)) {
    for (const key of ['id','task_id']) {
      if (part[key] !== undefined) {
        found = true;
        if (String(part[key]) !== expectedId) throw new ContractViolation('Poll result belongs to another task');
      }
    }
  }
  if (!found) throw new ContractViolation('Poll response does not prove the queried task identity');
}
