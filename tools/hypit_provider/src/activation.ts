import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { createRuntimeEndpointAdapterFacet, runtimeConfigExact, runtimeConfigObject,
  runtimeConfigString } from '@hypit/runtime-local/extension';
import { createOfficialProvider, OperationLedger, providerName } from './official-provider.js';
import { loopbackBackend } from './loopback-backend.js';

/** Installable, genuine Runtime activation; no live backend is exposed by project configuration. */
export default {
  format: 'hypit.package@1' as const,
  facets: [createRuntimeEndpointAdapterFacet({
    use: providerName,
    activate(context) {
      const config = runtimeConfigObject(context.config, 'OpenLux');
      runtimeConfigExact(config, ['account','bridgeUrl','bridgeToken','resumeKnownTask','allowReferences','taskType'], 'OpenLux');
      if (config.resumeKnownTask !== undefined && typeof config.resumeKnownTask !== 'boolean') throw new Error('Invalid recovery policy');
      if (config.allowReferences !== undefined && typeof config.allowReferences !== 'boolean') throw new Error('Invalid reference policy');
      if (config.taskType !== undefined && !['reference','edit','extend'].includes(String(config.taskType))) throw new Error('Invalid task type');
      const account = runtimeConfigString(config.account, 'account');
      if (!context.pool || !account || !/^[A-Za-z0-9_.:-]{1,128}$/.test(account)) throw new Error('Explicit account and pool required');
      mkdirSync(context.dataRoot, { recursive: true });
      const ledger = new OperationLedger(resolve(context.dataRoot, 'openlux-operations.db'));
      const hasBridge = config.bridgeUrl !== undefined || config.bridgeToken !== undefined;
      const bridgeUrl = hasBridge ? runtimeConfigString(config.bridgeUrl,'bridgeUrl') : undefined;
      const bridgeToken = hasBridge ? runtimeConfigString(config.bridgeToken,'bridgeToken') : undefined;
      if (hasBridge && (!bridgeUrl || !bridgeToken)) throw new Error('Complete host capability required');
      const backend = bridgeUrl && bridgeToken ? loopbackBackend(bridgeUrl,bridgeToken) : undefined;
      return { endpoint: createOfficialProvider({ instance: context.instance, pool: context.pool, account, ledger,
        ...(backend ? {backend} : {}), resumeKnownTask: config.resumeKnownTask === true,
        allowReferences: config.allowReferences === true,
        ...(config.taskType ? {taskType:config.taskType as 'reference'|'edit'|'extend'} : {}) }),
        diagnose: () => backend ? [] : [{ severity: 'error' as const, code: 'OPENLUX_ACCOUNT_UNVERIFIED',
          message: 'Account protocol, exact quote and server spending authorization are not verified; generation disabled' }] };
    },
  })],
};
