// Pure frontend contract; no browser or provider calls are represented by this test.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
test('adding an account copies protocol preferences but never a saved credential reference',()=>{
 const source=fs.readFileSync(path.join(__dirname,'../static/configuration.js'),'utf8');
 const context={window:{document:{readyState:'loading',addEventListener(){}}},structuredClone,
   crypto:{randomUUID:()=> '12345678-1234-1234-1234-123456789abc'}};
 vm.runInNewContext(source.replace('  async function mount() {','  window.testCloneAccount=cloneProviderAccount;\n  async function mount() {'),context);
 const original={id:'official-account',name:'Existing',credential_ref:'cred_0123456789abcdef0123456789abcdef',
   key_configured:true,legacy_credential:true,enabled:true,base_url:'https://api.openlux.ai',models:{chat:'my-model'}};
 const row=context.window.testCloneAccount(original);
 assert.equal(row.credential_ref,undefined);assert.equal(row.key_configured,false);assert.equal(row.legacy_credential,false);
 assert.notEqual(row.id,original.id);assert.equal(row.base_url,original.base_url);assert.equal(row.models.chat,'my-model');
 assert.equal(original.credential_ref,'cred_0123456789abcdef0123456789abcdef');
 row.models.chat='changed';assert.equal(original.models.chat,'my-model');
});
