import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { seedanceEndpointsByModel, sealSeedanceRequest } from '@hypit/seedance';
import { RuntimeAdapterRegistry } from '@hypit/runtime-local/extension';
import { describeRuntimeConfigProviders } from '@hypit/runtime-local';
import activation from '../src/activation.js';
import { createOfficialProvider, OperationLedger, officialPayload, providerName } from '../src/official-provider.js';
const model = seedanceEndpointsByModel['seedance-2.5'];
function fixture() {
    const root = mkdtempSync(join(tmpdir(), 'seedance-sdk-'));
    const ledger = new OperationLedger(join(root, 'ledger.db'));
    const calls = { authorize: 0, submit: 0, poll: 0, collect: 0 };
    const bytes = new Uint8Array(readFileSync('tests/real_sample.mp4'));
    const backend = {
        async authorize() { calls.authorize++; },
        async submit() { calls.submit++; return 'remote-1'; },
        async poll() { calls.poll++; return { state: 'ready' }; },
        async collect() { calls.collect++; return bytes; },
    };
    const request = sealSeedanceRequest('seedance-2.5', { prompt: ['A product demonstration'],
        duration: [4], aspectRatio: ['9:16'], resolution: ['720p'], generateAudio: [false], webSearch: [false] });
    const checkpoints = [];
    const context = {
        command: { kind: 'fulfill-need', id: 'cmd-1', need: { id: 'need-1', capability: model.capability,
                returns: model.returns, constraints: request, result: 'record-1' } },
        need: { id: 'need-1', capability: model.capability, returns: model.returns,
            constraints: request, result: 'record-1' },
        operation: 'operation-1', credentials: {},
        async checkpoint(cp) { checkpoints.push(cp); },
        resources: { async put(data, mediaType) { return { kind: 'blob', resource: 'res_fixture', size: data.length, mediaType }; },
            async get() { return undefined; }, async has() { return false; }, async write() { } },
    };
    const configure = (port = backend) => {
        let endpoint;
        let registration;
        const provider = createOfficialProvider({ instance: 'openlux.test', pool: 'test', account: 'offline', ledger,
            ...(port ? { backend: port } : {}) });
        provider.install({ registerImmediateEndpoint() { throw new Error('Unexpected immediate registration'); },
            registerAsyncEndpoint(_id, capability, returns, ep, options) {
                assert.deepEqual(capability, model.capability);
                assert.deepEqual(returns, model.returns);
                endpoint = ep;
                registration = options;
            } });
        return { endpoint, provider, get registration() { return registration; } };
    };
    return { root, ledger, calls, backend, context, checkpoints, configure,
        close() { ledger.close(); rmSync(root, { recursive: true, force: true }); } };
}
function pollContext(context, outcome) {
    assert.ok('handle' in outcome);
    return { ...context, handle: outcome.handle };
}
test('real SDK exact model mapping and endpoint scheduling', () => {
    const f = fixture();
    try {
        const registered = f.configure();
        assert.equal(officialPayload(f.context.need).model, 'doubao-seedance-2-5-260628');
        assert.equal(registered.registration?.supports?.(f.context.need).status, 'supported');
        assert.ok(registered.registration?.scheduling?.resources.some(r => r.limit === 10));
        assert.ok(registered.registration?.scheduling?.actions?.collect);
    }
    finally {
        f.close();
    }
});
test('real SDK start checkpoint poll collect returns generated video-set', async () => {
    const f = fixture();
    try {
        const { endpoint } = f.configure();
        const start = await endpoint.start(f.context);
        assert.equal(start.status, 'pending');
        assert.equal(f.checkpoints[0]?.receipt?.id, 'remote-1');
        const poll = await endpoint.poll(pollContext(f.context, start));
        assert.equal(poll.status, 'ready');
        assert.equal(f.checkpoints[1]?.remoteEnded, true);
        const collect = await endpoint.collect(pollContext(f.context, poll));
        assert.equal(collect.status, 'completed');
        assert.deepEqual(f.calls, { authorize: 1, submit: 1, poll: 1, collect: 1 });
    }
    finally {
        f.close();
    }
});
test('default provider refuses paid submission with no backend authority', async () => {
    const f = fixture();
    try {
        const { endpoint } = f.configure(undefined);
        // Explicitly construct without trusted backend (configure defaults to fixture).
        let ep;
        const blocked = createOfficialProvider({ instance: 'blocked', pool: 'test', account: 'offline', ledger: f.ledger });
        assert.equal(blocked.offers[0]?.supports?.(f.context.need).status, 'unsupported');
        blocked.install({
            registerImmediateEndpoint() { }, registerAsyncEndpoint(_i, _c, _r, value) { ep = value; }
        });
        assert.equal((await ep.start(f.context)).status, 'failed');
        assert.equal(f.calls.submit, 0);
        assert.equal(f.ledger.read('operation-1'), undefined);
        assert.ok(endpoint);
    }
    finally {
        f.close();
    }
});
test('backend submit timeout quarantines even if remote keeps running', async () => {
    const f = fixture();
    try {
        f.backend.submit = async () => { f.calls.submit++; return new Promise(() => { }); };
        let endpoint;
        createOfficialProvider({ instance: 'timeout', pool: 'test', account: 'offline', ledger: f.ledger, backend: f.backend, actionTimeoutMs: 10 }).install({
            registerImmediateEndpoint() { }, registerAsyncEndpoint(_i, _c, _r, e) { endpoint = e; }
        });
        assert.equal((await endpoint.start(f.context)).status, 'failed');
        assert.equal((await endpoint.start(f.context)).status, 'failed');
        assert.equal(f.calls.submit, 1);
        assert.equal(f.ledger.read('operation-1')?.state, 'unknown');
    }
    finally {
        f.close();
    }
});
test('red team: crash after remote accept quarantines operation across reopen', async () => {
    const f = fixture();
    try {
        f.backend.submit = async () => { f.calls.submit++; throw new Error('lost acknowledgement SECRET'); };
        const { endpoint } = f.configure();
        const start = await endpoint.start(f.context);
        assert.equal(start.status, 'failed');
        assert.ok(!JSON.stringify(start).includes('SECRET'));
        f.ledger.close();
        const reopened = new OperationLedger(join(f.root, 'ledger.db'));
        try {
            let ep;
            createOfficialProvider({ instance: 'retry', pool: 'test', account: 'offline', ledger: reopened, backend: f.backend }).install({
                registerImmediateEndpoint() { }, registerAsyncEndpoint(_i, _c, _r, e) { ep = e; }
            });
            assert.equal((await ep.start(f.context)).status, 'failed');
            assert.equal(f.calls.submit, 1);
        }
        finally {
            reopened.close();
        }
    }
    finally {
        rmSync(f.root, { recursive: true, force: true });
    }
});
test('red team: duplicate concurrent starts and a new operation cannot charge twice', async () => {
    const f = fixture();
    try {
        const { endpoint } = f.configure();
        await Promise.all([endpoint.start(f.context), endpoint.start(f.context)]);
        assert.equal(f.calls.submit, 1);
        assert.equal((await endpoint.start({ ...f.context, operation: 'operation-2' })).status, 'failed');
        assert.equal(f.calls.submit, 1);
    }
    finally {
        f.close();
    }
});
test('unverified media, web search and exact model changes refused before submit', () => {
    const f = fixture();
    try {
        const request = f.context.need;
        assert.throws(() => officialPayload({ ...request, capability: { ...model.capability, name: 'seedance-2-mini' } }));
        assert.throws(() => officialPayload({ ...request, pendingInputs: [{ input: 'referenceVideo' }] }));
        const constraints = structuredClone(request.constraints);
        constraints.ports.webSearch = [true];
        assert.throws(() => officialPayload({ ...request, constraints: constraints }));
        assert.equal(f.calls.submit, 0);
    }
    finally {
        f.close();
    }
});
test('unknown remote state and forged handle cannot collect', async () => {
    const f = fixture();
    try {
        const { endpoint } = f.configure();
        const start = await endpoint.start(f.context);
        f.backend.poll = async () => ({ state: 'surprise' });
        assert.equal((await endpoint.poll(pollContext(f.context, start))).status, 'failed');
        await assert.rejects(async () => endpoint.collect({ ...f.context, handle: { id: 'other', fingerprint: 'fake' } }));
        assert.equal((await endpoint.collect(pollContext(f.context, start))).status, 'failed');
        assert.equal(f.calls.collect, 0);
    }
    finally {
        f.close();
    }
});
test('cancel is unsupported, never claims remote cancellation', async () => {
    const f = fixture();
    try {
        const { endpoint } = f.configure();
        const start = await endpoint.start(f.context);
        assert.deepEqual(await endpoint.cancel(pollContext(f.context, start)), { status: 'unsupported' });
    }
    finally {
        f.close();
    }
});
test('official Runtime Profile activates real package facet and selects exact model', async () => {
    const root = mkdtempSync(join(tmpdir(), 'seedance-profile-'));
    try {
        const profile = join(root, 'hypit.runtime.json');
        writeFileSync(profile, JSON.stringify({ format: 'hypit.runtime-local@1', dataRoot: join(root, 'data'),
            credentials: {}, endpoints: { 'openlux.test': { use: providerName, pool: 'offline', config: { account: 'offline' } } },
            bindings: { '@hypit/seedance@1#seedance-2.5': 'openlux.test' } }));
        const registry = new RuntimeAdapterRegistry();
        for (const facet of activation.facets)
            registry.registerFacet(facet);
        const f = fixture();
        const query = { request: 'request-1', capability: model.capability, returns: model.returns, constraints: f.context.need.constraints };
        f.close();
        const providers = await describeRuntimeConfigProviders(profile, [query], { registry, packageRoot: resolve('.'), distributionPackageRoot: resolve('node_modules/@hypit/hypit'), hostStateRoot: join(root, 'host') });
        assert.ok(JSON.stringify(providers).includes('openlux.test'));
    }
    finally {
        rmSync(root, { recursive: true, force: true });
    }
});
