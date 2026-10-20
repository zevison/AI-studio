import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.path=self.root/'studio.db';self.asset=self.root/'original.mp4';self.asset.write_bytes(b'keep history')
        c=sqlite3.connect(self.path)
        c.executescript('''CREATE TABLE assets(id TEXT PRIMARY KEY,path TEXT);
        CREATE TABLE tasks(id TEXT PRIMARY KEY,status TEXT,request_json TEXT);
        CREATE TABLE generation_previews(token TEXT,payload_json TEXT,expires_at REAL);
        CREATE TABLE product_sources(id TEXT PRIMARY KEY,product_id TEXT,status TEXT);
        CREATE TABLE hypit_projects(id TEXT PRIMARY KEY,status TEXT,asset_ids_json TEXT);''')
        c.execute('INSERT INTO assets VALUES(?,?)',('asset_1',str(self.asset)))
        c.execute('INSERT INTO assets VALUES(?,?)',('asset_2',str(self.asset)))
        c.execute('INSERT INTO product_sources VALUES(?,?,?)',('source_1','product_1','ready'))
        c.execute('INSERT INTO generation_previews VALUES(?,?,?)',('preview',json.dumps({'asset_id':'asset_1'}),time.time()+1000));c.commit();c.close()
        package=types.ModuleType('lifecycle_test');package.__path__=[]
        cfg=types.ModuleType('lifecycle_test.config');cfg.DB_PATH=self.path;package.config=cfg
        sys.modules['lifecycle_test']=package;sys.modules['lifecycle_test.config']=cfg
        spec=importlib.util.spec_from_file_location('lifecycle_test.upload_lifecycle',str(Path(__file__).resolve().parents[1]/'core/upload_lifecycle.py'))
        self.mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.mod);self.mod.ensure()
    def query(self,sql,args=()):
        c=sqlite3.connect(self.path)
        try:
            rows=c.execute(sql,args).fetchall();c.commit();return rows
        finally:c.close()
    def test_remove_keeps_historical_bytes_and_records(self):
        self.mod.remove('asset','asset_1')
        self.assertTrue(self.asset.exists());self.assertEqual(len(self.query('SELECT * FROM assets')),2)
        self.assertEqual(self.mod.filter_rows([{'id':'asset_1'},{'id':'asset_2'}]),[{'id':'asset_2'}])
    def test_preview_invalidated(self):
        self.mod.remove('asset','asset_1');self.assertEqual(self.query('SELECT expires_at FROM generation_previews')[0][0],0)
    def test_remove_idempotent_and_restore(self):
        self.mod.remove('asset','asset_1');self.mod.remove('asset','asset_1');self.mod.restore('asset','asset_1')
        self.assertFalse(self.mod.removed('asset','asset_1'))
    def test_active_reference_prevents_remove(self):
        self.query('INSERT INTO tasks VALUES(?,?,?)',('t','running',json.dumps({'assets':['asset_1']})))
        with self.assertRaises(ValueError):self.mod.remove('asset','asset_1')
        self.assertFalse(self.mod.removed('asset','asset_1'))
    def test_uncertain_reference_prevents_remove(self):
        self.query('INSERT INTO tasks VALUES(?,?,?)',('t','submission_unknown',json.dumps({'assets':['asset_1']})))
        with self.assertRaises(ValueError):self.mod.remove('asset','asset_1')
    def test_stale_task_insert_blocked_atomically(self):
        self.mod.remove('asset','asset_1')
        with self.assertRaises(sqlite3.IntegrityError):self.query('INSERT INTO tasks VALUES(?,?,?)',('t','pending',json.dumps({'assets':['asset_1']})))
    def test_other_assets_not_blocked(self):
        self.mod.remove('asset','asset_1')
        self.query('INSERT INTO tasks VALUES(?,?,?)',('t','pending',json.dumps({'assets':['asset_2']})))
        self.mod.validate_request({'assets':['asset_2']})
    def test_stale_request_rejected(self):
        self.mod.remove('asset','asset_1')
        with self.assertRaises(ValueError):self.mod.validate_request({'nested':[{'asset_id':'asset_1'}]})
    def test_old_hypit_draft_cannot_build_removed_asset(self):
        self.query('INSERT INTO hypit_projects VALUES(?,?,?)',('hp_old','draft',json.dumps(['asset_1'])))
        self.mod.remove('asset','asset_1')
        with self.assertRaises(ValueError):self.mod.validate_request({'id':'hp_old'})
    def test_product_context_checked(self):
        with self.assertRaises(ValueError):self.mod.remove('source','source_1','other_product')
    def test_product_memory_stale_after_removal(self):
        self.mod.remove('source','source_1','product_1')
        self.assertTrue(self.mod.memory_stale('product_1',0));self.assertFalse(self.mod.memory_stale('other',0))
        self.assertEqual(self.mod.filter_rows([{'id':'source_1'}],'source'),[])
    def test_concurrent_remove_and_insert_has_no_unsafe_outcome(self):
        outcomes=[];barrier=threading.Barrier(2)
        def remove():
            barrier.wait()
            try:self.mod.remove('asset','asset_1');outcomes.append('removed')
            except ValueError:outcomes.append('protected')
        def submit():
            barrier.wait()
            try:self.query('INSERT INTO tasks VALUES(?,?,?)',('t','pending',json.dumps({'assets':['asset_1']})));outcomes.append('inserted')
            except sqlite3.IntegrityError:outcomes.append('blocked')
        a=threading.Thread(target=remove);b=threading.Thread(target=submit);a.start();b.start();a.join();b.join()
        self.assertFalse('removed' in outcomes and 'inserted' in outcomes)
        self.assertEqual(len(outcomes),2)

if __name__=='__main__':unittest.main(verbosity=2)
