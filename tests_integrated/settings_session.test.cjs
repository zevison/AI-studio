const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness(failFirst = false) {
  const calls = [];
  const window = {location: {href: 'http://127.0.0.1:1234/', origin: 'http://127.0.0.1:1234'}};
  window.fetch = async (input, options) => {
    calls.push({input, options});
    if (input === '/api/hypit/session') {
      const ok = !(failFirst && calls.filter(c => c.input === input).length === 1);
      return {ok, json: async () => ({ok, data: {token: 'test-session-only'}})};
    }
    return {ok: true};
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/settings_session.js'), 'utf8'), {window, URL, Headers, Request, Set, Error});
  return {window, calls};
}

test('configuration writes share an in-memory session and preserve existing headers', async () => {
  const {window, calls} = harness();
  await Promise.all(['/api/settings', '/api/configuration'].map(input => window.fetch(input, {method: 'POST', headers: {'Content-Type': 'application/json'}})));
  assert.equal(calls.filter(c => c.input === '/api/hypit/session').length, 1);
  for (const call of calls.filter(c => c.options)) {
    assert.equal(call.options.headers.get('X-Hypit-Session'), 'test-session-only');
    assert.equal(call.options.headers.get('Content-Type'), 'application/json');
  }
});

test('session is never sent across origins or to unrelated business endpoints', async () => {
  const {window, calls} = harness();
  await window.fetch('https://example.com/api/settings', {method: 'POST'});
  await window.fetch('/api/video/generate', {method: 'POST'});
  await window.fetch('/api/settings');
  assert.equal(calls.length, 3);
  assert.ok(calls.every(c => !c.options?.headers));
});

test('failed session acquisition does not issue the write and may be retried explicitly', async () => {
  const {window, calls} = harness(true);
  await assert.rejects(window.fetch('/api/settings', {method: 'POST'}));
  assert.equal(calls.filter(c => c.input === '/api/settings').length, 0);
  await window.fetch('/api/settings', {method: 'POST'});
  assert.equal(calls.filter(c => c.input === '/api/settings').length, 1);
});

test('plugin writes and binary offline uploads receive session without changing content type',async()=>{
  const {window,calls}=harness();
  for(const route of ['/api/components/download','/api/components/preferences','/api/components/repair','/api/components/uninstall','/api/components/check','/api/components/update','/api/components/rollback'])
    await window.fetch(route,{method:'POST',headers:{'Content-Type':'application/json'}});
  await window.fetch('/api/components/import?id=asr',{method:'POST',headers:{'Content-Type':'application/zip'},body:'fixture'});
  for(const call of calls.filter(c=>String(c.input).startsWith('/api/components/')))
    assert.equal(call.options.headers.get('X-Hypit-Session'),'test-session-only');
  assert.equal(calls.at(-1).options.headers.get('Content-Type'),'application/zip');
});
