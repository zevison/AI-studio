/** Exact-request, short-lived host capability; never carries an OpenLux API key. */
import type { BackendPort, PollResult } from './official-provider.js';

export function loopbackBackend(address: string, token: string): BackendPort {
  const url = new URL(address);
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port ||
      url.username || url.password || url.pathname !== '/' || url.search || url.hash ||
      !/^[A-Za-z0-9_-]{64}$/.test(token)) throw new Error('Invalid host Runtime capability');
  async function call(path: string, body: unknown, binary = false): Promise<unknown> {
    const response = await fetch(address + path, {
      method: 'POST', redirect: 'error', signal: AbortSignal.timeout(115_000),
      headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!response.ok) { await response.body?.cancel(); throw new Error('Host Runtime action rejected; reconcile before retry'); }
    const limit = binary ? 400 * 1024 * 1024 : 256 * 1024;
    const declared = Number(response.headers.get('content-length'));
    if (!Number.isSafeInteger(declared) || declared <= 0 || declared > limit) {
      await response.body?.cancel(); throw new Error('Host response length invalid');
    }
    if (response.headers.get('content-type') !== (binary ? 'video/mp4' : 'application/json')) {
      await response.body?.cancel(); throw new Error('Host response type invalid');
    }
    const reader = response.body!.getReader();
    const chunks: Uint8Array[] = []; let count = 0;
    try {
      for (;;) {
        const part = await reader.read(); if (part.done) break;
        count += part.value.length;
        if (count > declared || count > limit) { await reader.cancel(); throw new Error('Host response overflow'); }
        chunks.push(part.value);
      }
    } finally { reader.releaseLock(); }
    if (count !== declared) throw new Error('Host response truncated');
    const bytes = Buffer.concat(chunks, count);
    return binary ? bytes : JSON.parse(bytes.toString('utf8'));
  }
  return {
    async authorize(payload) { await call('/authorize', { payload }); },
    async submit(payload, _identity) {
      const result = await call('/submit', { payload }) as {id?: unknown};
      if (typeof result.id !== 'string') throw new Error('Host acknowledgement missing');
      return result.id;
    },
    async poll(id) {
      const result = await call('/poll', { id }) as PollResult;
      if (!['pending','ready','failed'].includes(result.state)) throw new Error('Host poll state invalid');
      return result;
    },
    async collect(id) { return await call('/collect', { id }, true) as Uint8Array; },
    async resolveReference(reference) {
      const response=await call('/reference',reference) as {url?: unknown};
      if (typeof response.url!=='string') throw new Error('Reference was not authorized by host');
      return response.url;
    },
  };
}
