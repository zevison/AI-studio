import importlib.util,json,os,sqlite3,sys,tempfile,threading,time,types,unittest
from contextlib import contextmanager,closing
from pathlib import Path
from unittest.mock import patch
BASE=Path(__file__).resolve().parents[1]
@contextmanager
def sql(path):
 with closing(sqlite3.connect(path)) as c:
  with c:yield c

class SafetyTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.data=self.root/'data';self.data.mkdir()
  for name in ('assets','outputs','cache','product_knowledge'):(self.data/name).mkdir()
  self.config=types.SimpleNamespace(ROOT=self.root,DATA_ROOT=self.data,USER_HOME=self.root,DATA_DIR=self.data,CACHE_DIR=self.data/'cache',DB_PATH=self.data/'studio.db',WORKSPACE_ID='owner')
  package=types.ModuleType('testspace');package.__path__=[];sys.modules['testspace']=package;sys.modules['testspace.config']=self.config
  # Admission uses the real shared upgrade gate against this fixture's own
  # control connection. Do not replace it with an always-open test stub.
  from core import update_service
  sys.modules['testspace.update_service']=update_service
  self.addCleanup(lambda:sys.modules.pop('testspace.update_service',None))
  for name in ('upload_lifecycle','team_safety','storage_manager'):
   file=BASE/'core'/f'{name}.py'
   if name=='upload_lifecycle' and not file.exists():file=Path('work/video-fix-uploads/core/upload_lifecycle.py')
   spec=importlib.util.spec_from_file_location('testspace.'+name,file);module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module);setattr(self,name,module)
  with sql(self.config.DB_PATH) as c:
   c.executescript('CREATE TABLE tasks(id TEXT PRIMARY KEY,status TEXT,request_json TEXT,kind TEXT,created_at REAL,updated_at REAL);CREATE TABLE assets(id TEXT PRIMARY KEY,path TEXT,stored_name TEXT);CREATE TABLE product_sources(id TEXT,stored_path TEXT,product_id TEXT);CREATE TABLE generation_previews(payload_json TEXT,expires_at REAL);')
  self.env=patch.dict(os.environ,{'STUDIO_CONTROL_DB':str(self.data/'team-control.sqlite'),'STUDIO_DISK_RESERVE_GB':'0','STUDIO_WORKSPACE_QUOTA_GB':'20'});self.env.start();self.addCleanup(self.env.stop)
 def asset(self):
  path=self.data/'assets/test.png';path.write_bytes(b'original')
  with sql(self.config.DB_PATH) as c:c.execute('INSERT INTO assets VALUES(?,?,?)',('asset1',str(path),path.name))
  return path
 def test_update_gate_rejects_paid_and_ordinary_mutation_admission(self):
  from core.update_service import _gate_schema
  from core.update_security import UpdateBlocked
  with self.team_safety.connection() as c:
   _gate_schema(c);c.execute("INSERT INTO studio_update_gate VALUES(1,'controlled-test','prepared',?)",(time.time(),));c.commit()
  with self.assertRaises(UpdateBlocked):self.team_safety.claim('/api/image/generate',{'prompt':'blocked'})
  with self.assertRaises(UpdateBlocked):
   with self.team_safety.mutation_scope():self.fail('Mutation started during update')
 def test_ordinary_mutation_registers_durable_busy_marker_and_cleans_it(self):
  with self.team_safety.mutation_scope():
   with self.team_safety.connection() as c:
    self.assertEqual(c.execute("SELECT status FROM update_request_writes").fetchall()[0][0],'in_flight')
  with self.team_safety.connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM update_request_writes').fetchone()[0],0)
 def test_same_payload_replays_receipt_without_new_operation(self):
  a=self.team_safety.claim('/api/image/generate',{'prompt':'same'});self.team_safety.finish(a['id'],200,b'{"ok":true}')
  b=self.team_safety.claim('/api/image/generate',{'prompt':'same'});self.assertTrue(b['replay']);self.assertEqual(b['id'],a['id'])
 def test_configured_external_output_is_managed_under_outputs_alias(self):
  self.config.OUTPUT_DIR=self.root/'单独 成片目录';self.config.OUTPUT_DIR.mkdir()
  result=self.config.OUTPUT_DIR/'export.mp4';result.write_bytes(b'local-export')
  rows=self.storage_manager.files('outputs')['rows']
  self.assertEqual(rows[0]['path'],'outputs/export.mp4')
  self.assertEqual(self.storage_manager.media_path('outputs/export.mp4'),result)
  moved=self.storage_manager.trash('outputs/export.mp4',True)
  self.assertFalse(result.exists());self.storage_manager.restore(moved['id'])
  self.assertEqual(result.read_bytes(),b'local-export')
  with self.assertRaises(PermissionError):self.storage_manager.media_path('outputs/../outside.txt')
 def test_concurrent_identical_requests_have_one_winner(self):
  barrier=threading.Barrier(2);results=[]
  def run():
   barrier.wait()
   try:results.append(self.team_safety.claim('/api/image/generate',{'prompt':'race'}))
   except self.team_safety.Refused:results.append(None)
  threads=[threading.Thread(target=run) for _ in range(2)]
  for t in threads:t.start()
  for t in threads:t.join()
  self.assertEqual(sum(x is not None for x in results),1)
 def test_unknown_response_never_replayed(self):
  a=self.team_safety.claim('/api/image/generate',{'prompt':'unknown'});self.team_safety.finish(a['id'],None,None)
  with self.assertRaises(self.team_safety.Refused):self.team_safety.claim('/api/image/generate',{'prompt':'unknown'})
 def test_key_cannot_change_parameters(self):
  a=self.team_safety.claim('/api/image/generate',{'prompt':'one'},'key');self.team_safety.finish(a['id'],200,b'{}')
  with self.assertRaises(self.team_safety.Refused):self.team_safety.claim('/api/image/generate',{'prompt':'two'},'key')
 def test_explicit_new_intent_is_allowed_after_acknowledged_response(self):
  a=self.team_safety.claim('/api/image/generate',{'prompt':'same'},'intent-one');self.team_safety.finish(a['id'],200,b'{}')
  b=self.team_safety.claim('/api/image/generate',{'prompt':'same'},'intent-two');self.assertFalse(b['replay'])
 def test_different_workspaces_have_separate_receipts(self):
  a=self.team_safety.claim('/api/image/generate',{'prompt':'same'});self.team_safety.finish(a['id'],200,b'owner')
  self.config.WORKSPACE_ID='other'
  b=self.team_safety.claim('/api/image/generate',{'prompt':'same'});self.assertFalse(b['replay'])
 def test_running_request_cannot_be_acknowledged(self):
  a=self.team_safety.claim('/api/image/generate',{'prompt':'wait'})
  with self.assertRaises(self.team_safety.Refused):self.team_safety.acknowledge(a['id'],True,'checked bill')
 def test_recover_marks_crashed_admission_unknown(self):
  a=self.team_safety.claim('/api/image/generate',{'prompt':'crash'})
  with self.team_safety.connection() as c:c.execute('UPDATE admissions SET pid=-100 WHERE id=?',(a['id'],));c.commit()
  self.team_safety.recover();self.assertEqual(self.team_safety.recent()[0]['state'],'unknown')
 def test_storage_trash_restore_preserves_bytes(self):
  p=self.asset();r=self.storage_manager.trash('assets/test.png',True);self.assertFalse(p.exists());self.assertTrue(self.upload_lifecycle.removed('asset','asset1'))
  self.storage_manager.restore(r['id']);self.assertEqual(p.read_bytes(),b'original');self.assertFalse(self.upload_lifecycle.removed('asset','asset1'))
 def test_historical_soft_deletion_preserved_after_restore(self):
  self.asset();self.upload_lifecycle.remove('asset','asset1');r=self.storage_manager.trash('assets/test.png',True);self.storage_manager.restore(r['id']);self.assertTrue(self.upload_lifecycle.removed('asset','asset1'))
 def test_permanent_delete_requires_confirmation(self):
  self.asset();r=self.storage_manager.trash('assets/test.png',True)
  with self.assertRaises(ValueError):self.storage_manager.purge(r['id'])
  self.assertTrue(self.storage_manager.purge(r['id'],True)['purged'])
 def test_path_traversal_refused(self):
  for path in ('../studio.db','assets/../../secret','.trash/item','studio.db','outputs/C:/x'):
   with self.assertRaises((PermissionError,ValueError)):self.storage_manager.trash(path,True)
 def test_active_task_protects_files(self):
  self.asset()
  with sql(self.config.DB_PATH) as c:c.execute('INSERT INTO tasks(id,status,request_json) VALUES(?,?,?)',('t','running','{}'))
  with self.assertRaises(ValueError):self.storage_manager.trash('assets/test.png',True)
  self.assertTrue((self.data/'assets/test.png').exists())
 def test_admission_reservation_protects_file_before_task_insert(self):
  self.asset();self.team_safety.claim('/api/image/generate',{'prompt':'before insert'})
  with self.assertRaises(ValueError):self.storage_manager.trash('assets/test.png',True)
 def test_restore_never_overwrites_new_file(self):
  p=self.asset();r=self.storage_manager.trash('assets/test.png',True);p.write_bytes(b'new')
  with self.assertRaises(ValueError):self.storage_manager.restore(r['id'])
  self.assertEqual(p.read_bytes(),b'new')
 def test_expiry_only_purges_old_trash(self):
  self.asset();r=self.storage_manager.trash('assets/test.png',True);self.assertFalse(self.storage_manager.purge(r['id'],True,True)['purged'])
  with self.storage_manager.database() as c:c.execute('UPDATE trash SET created=?',(time.time()-8*86400,));c.commit()
  self.storage_manager.maintenance();self.assertFalse((self.data/'.trash'/r['id']).exists())
 def test_cleanup_preserves_media_and_recent_cache(self):
  self.asset();cache=self.data/'cache/pose-install-check';cache.mkdir();old=cache/'old.png';old.write_bytes(b'old');os.utime(old,(0,0));new=cache/'new.png';new.write_bytes(b'new')
  self.storage_manager.maintenance();self.assertFalse(old.exists());self.assertTrue(new.exists());self.assertTrue((self.data/'assets/test.png').exists())
 def test_quota_prevents_admission(self):
  with patch.dict(os.environ,{'STUDIO_WORKSPACE_QUOTA_GB':'0'}):
   with self.assertRaises(ValueError):self.storage_manager.require_capacity(1)
 def test_disk_reserve_prevents_admission(self):
  with patch.object(self.storage_manager.shutil,'disk_usage',return_value=types.SimpleNamespace(free=0)),patch.dict(os.environ,{'STUDIO_DISK_RESERVE_GB':'5'}):
   with self.assertRaises(ValueError):self.storage_manager.require_capacity(1)
 def test_unreferenced_product_file_must_be_soft_removed_first(self):
  p=self.data/'product_knowledge/p.png';p.write_bytes(b'p')
  with sql(self.config.DB_PATH) as c:c.execute('INSERT INTO product_sources VALUES(?,?,?)',('src1',str(p),'product1'))
  with self.assertRaises(ValueError):self.storage_manager.trash('product_knowledge/p.png',True)
 def test_crashed_move_reconciles_both_paths(self):
  self.asset();r=self.storage_manager.trash('assets/test.png',True)
  with self.storage_manager.database() as c:c.execute("UPDATE trash SET state='moving'");c.commit()
  self.storage_manager.reconcile_moves();self.assertEqual(self.storage_manager.status()['trash'][0]['state'],'ready')

if __name__=='__main__':unittest.main(verbosity=2)
