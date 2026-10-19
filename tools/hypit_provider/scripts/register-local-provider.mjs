// Install our compiled activation into the trusted host package root, never
// into Agent-controlled projects. No network, symlinks, lifecycle scripts or npm.
import { mkdirSync, copyFileSync, readFileSync, readdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const destination=resolve(root,'node_modules/@seedance-studio/openlux-provider');
mkdirSync(resolve(destination,'dist/src'),{recursive:true});
const pkg=JSON.parse(readFileSync(resolve(root,'package.json'),'utf8'));
if(pkg.name!=='@seedance-studio/openlux-provider') throw new Error('Unexpected host package');
copyFileSync(resolve(root,'package.json'),resolve(destination,'package.json'));
for(const name of readdirSync(resolve(root,'dist/src'))) {
  if(!/^[a-z0-9-]+\.(js|d\.ts)$/.test(name)) continue;
  copyFileSync(resolve(root,'dist/src',name),resolve(destination,'dist/src',name));
}
console.log('Registered compiled Provider in trusted host package root');
