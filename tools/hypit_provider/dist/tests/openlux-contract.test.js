import test from 'node:test';
import assert from 'node:assert/strict';
import { buildOpenLuxVideoPayload, remoteTaskId, remoteState, finalVideoUrl, assertPollIdentity, requestFingerprint, externalHttps, ContractViolation } from '../src/openlux-contract.js';
const basic = () => ({ prompt: 'Generate a product video', taskType: 'text', references: [], duration: 8, ratio: '9:16', resolution: '720p', generateAudio: true });
test('text-only Seedance request is deterministic', () => {
    const payload = buildOpenLuxVideoPayload(basic());
    assert.equal(payload.model, 'doubao-seedance-2-5-260628');
    assert.equal(payload.duration, 8);
    assert.deepEqual(payload.content, [{ type: 'text', text: 'Generate a product video' }]);
    assert.equal(payload.omni_reference_task_type, undefined);
});
test('paid edit without source video fails locally', () => assert.throws(() => buildOpenLuxVideoPayload({ ...basic(), taskType: 'edit', ratio: 'adaptive', duration: -1 }), ContractViolation));
test('unknown task type rejected', () => assert.throws(() => buildOpenLuxVideoPayload({ ...basic(), taskType: 'something' }), ContractViolation));
test('invalid output duration rejected', () => assert.throws(() => buildOpenLuxVideoPayload({ ...basic(), duration: 200 }), ContractViolation));
test('boolean duration rejected', () => assert.throws(() => buildOpenLuxVideoPayload({ ...basic(), duration: true }), ContractViolation));
test('invalid ratio rejected', () => assert.throws(() => buildOpenLuxVideoPayload({ ...basic(), ratio: 'extreme' }), ContractViolation));
test('no silent dropping of unknown reference roles', () => assert.throws(() => buildOpenLuxVideoPayload({ ...basic(), taskType: 'reference', references: [{ role: 'hidden', url: 'https://assets.example.com/a.mp4' }] }), ContractViolation));
test('no silent loss of person reference classification', () => assert.throws(() => buildOpenLuxVideoPayload({ ...basic(), taskType: 'reference', references: [{ role: 'reference_image', url: 'https://assets.example.com/a.png', personReference: true }] }), /unverified/));
test('no assumed person-reference when missing', () => assert.throws(() => buildOpenLuxVideoPayload({ ...basic(), taskType: 'reference', references: [{ role: 'reference_image', url: 'https://assets.example.com/a.png' }] }), /required/));
test('audio cannot be person-reference', () => assert.throws(() => buildOpenLuxVideoPayload({ ...basic(), taskType: 'reference', references: [{ role: 'reference_audio', url: 'https://assets.example.com/a.mp3', personReference: false }] }), ContractViolation));
test('audio-only reference is represented explicitly', () => {
    const payload = buildOpenLuxVideoPayload({ ...basic(), taskType: 'reference', references: [{ role: 'reference_audio', url: 'https://assets.example.com/a.mp3' }] });
    assert.deepEqual(payload.content[1], { type: 'audio_url', audio_url: { url: 'https://assets.example.com/a.mp3' }, role: 'reference_audio' });
});
test('unknown reference attribute cannot be dropped', () => assert.throws(() => buildOpenLuxVideoPayload({ ...basic(), taskType: 'reference', references: [{ role: 'reference_audio', url: 'https://assets.example.com/a.mp3', special: 1 }] }), ContractViolation));
test('reject local hosts and credentials', () => {
    for (const x of ['http://assets.example.com/a', 'https://127.0.0.1/a', 'https://localhost/a', 'https://10.0.0.4/a', 'https://user:pass@assets.example.com/a', 'https://assets.example.com:8443/a', 'https://[::1]/a', 'file:///etc/passwd'])
        assert.throws(() => externalHttps(x), ContractViolation);
});
test('reject conflicting remote IDs', () => assert.throws(() => remoteTaskId({ id: 'abc', data: { task_id: 'xyz' } }), ContractViolation));
test('reject missing remote ID', () => assert.throws(() => remoteTaskId({ success: true }), ContractViolation));
test('accept same task identity in both nodes', () => assert.equal(remoteTaskId({ id: 'abc', data: { task_id: 'abc' } }), 'abc'));
test('unknown state fails closed', () => assert.throws(() => remoteState({ status: 'working' }), ContractViolation));
test('status conflict fails closed', () => assert.throws(() => remoteState({ status: 'running', data: { status: 'succeeded' } }), ContractViolation));
test('recognize explicit states', () => {
    assert.equal(remoteState({ status: 'running' }), 'pending');
    assert.equal(remoteState({ status: 'succeeded' }), 'ready');
    assert.equal(remoteState({ status: 'failed' }), 'failed');
});
test('reject success without signed result URL', () => assert.throws(() => finalVideoUrl({ status: 'succeeded' }), ContractViolation));
test('reject conflicting result URLs', () => assert.throws(() => finalVideoUrl({ video_url: 'https://assets.example.com/1.mp4', output_url: 'https://assets.example.com/2.mp4' }), ContractViolation));
test('accept explicit result URL', () => assert.equal(finalVideoUrl({ data: { output_url: 'https://assets.example.com/ok.mp4' } }), 'https://assets.example.com/ok.mp4'));
test('poll task identity mismatch rejected', () => assert.throws(() => assertPollIdentity({ task_id: 'other', status: 'ready' }, 'real'), ContractViolation));
test('poll response without identity is not proof of identity', () => { assert.throws(() => assertPollIdentity({ status: 'pending' }, 'real'), ContractViolation); });
test('payload fingerprint binds account', () => {
    const p = buildOpenLuxVideoPayload(basic());
    assert.notEqual(requestFingerprint('accountA', p), requestFingerprint('accountB', p));
});
test('same semantically identical payload uses one fingerprint regardless of key order', () => {
    const p = buildOpenLuxVideoPayload(basic());
    const q = Object.fromEntries(Object.entries(p).reverse());
    assert.equal(requestFingerprint('one', p), requestFingerprint('one', q));
});
test('public CDN IP not falsely treated as local', () => assert.equal(externalHttps('https://172.217.10.1/file.mp4'), 'https://172.217.10.1/file.mp4'));
test('private 172.16.0.0/12 address rejected', () => assert.throws(() => externalHttps('https://172.21.33.1/private'), ContractViolation));
