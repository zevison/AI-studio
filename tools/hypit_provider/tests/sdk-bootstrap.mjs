// Same official Distribution resolver used by bin/hypit.mjs; no service calls.
import { register } from 'tsx/esm/api';
import { realpathSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
register();
const root = realpathSync.native(resolve('node_modules/@hypit/hypit'));
const { installDistributionPackageResolution } = await import(pathToFileURL(
  resolve(root, 'packages/loader/src/node/distribution-resolution.ts')).href);
installDistributionPackageResolution([root]);
