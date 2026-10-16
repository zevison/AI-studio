import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { createRuntimeEndpointAdapterFacet, runtimeConfigExact, runtimeConfigObject, runtimeConfigString } from '@hypit/runtime-local/extension';
import { createOfficialProvider, OperationLedger, providerName } from './official-provider.js';
/** Installable, genuine Runtime activation; no live backend is exposed by project configuration. */
export default {
    format: 'hypit.package@1',
    facets: [createRuntimeEndpointAdapterFacet({
            use: providerName,
            activate(context) {
                const config = runtimeConfigObject(context.config, 'OpenLux');
                runtimeConfigExact(config, ['account'], 'OpenLux');
                const account = runtimeConfigString(config.account, 'account');
                if (!context.pool || !account || !/^[A-Za-z0-9_.:-]{1,128}$/.test(account))
                    throw new Error('Explicit account and pool required');
                mkdirSync(context.dataRoot, { recursive: true });
                const ledger = new OperationLedger(resolve(context.dataRoot, 'openlux-operations.db'));
                return { endpoint: createOfficialProvider({ instance: context.instance, pool: context.pool, account, ledger }),
                    diagnose: () => [{ severity: 'error', code: 'OPENLUX_ACCOUNT_UNVERIFIED',
                            message: 'Account protocol, exact quote and server spending authorization are not verified; generation disabled' }] };
            },
        })],
};
