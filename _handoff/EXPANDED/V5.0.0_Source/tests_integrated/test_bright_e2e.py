# coding: utf-8
"""Offline regression for merging BrightData_E2E_Pilot_V2.5 into the full Studio.
Does NOT call a provider, access TikTok, read Chrome cookies, or charge OpenLux.
"""
from pathlib import Path
from unittest.mock import patch
import tempfile,sys,json,hashlib,subprocess,time
HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
from core import config,db,bright_tiktok,hot_radar,viral_review,bright_e2e_media,media

with tempfile.TemporaryDirectory(prefix='studio_e2e_merge_') as temp:
    t=Path(temp)
    db.DB_PATH=t/'db.sqlite'
    config.DB_PATH=t/'db.sqlite'
    config.CACHE_DIR=t/'cache';config.CACHE_DIR.mkdir(parents=True)
    config.ASSET_DIR=t/'assets';config.ASSET_DIR.mkdir(parents=True)
    db.init_db();hot_radar.ensure_schema();bright_tiktok.schema();viral_review.schema()
    pid='test_product'
    db.execute('''INSERT INTO radar_products(id,name,market,keywords_json,facts_json,restrictions_json,active,created_at,updated_at,is_draft)
        VALUES(?,?,?,?,?,?,?,?,?,?)''',(pid,'Test Portable Pump','US','["portable air pump"]','[]','[]',1,time.time(),time.time(),0))
    job='bd_offline_test'
    db.execute('INSERT INTO bt_jobs(id,product_id,quantity,status,snapshot_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
               (job,pid,10,'ready','snapshot_testing',time.time(),time.time()))
    post='https://www.tiktok.com/@outdoor_test/video/7690123456789012345'
    records=[
      {'error':'dead_page','error_code':'dead_page','url':post},
      {'url':post,'post_id':'7690123456789012345','post_type':'video','description':'Portable pump demo',
       'create_time':'2026-10-13T08:00:00Z','play_count':'12500','digg_count':90,'share_count':'13',
       'region':'US','video_url':'https://example.tiktokcdn.com/v.mp4','preview_image':'https://cdn.example.test/poster.jpg'},
      {'url':'https://www.tiktok.com/@outdoor_test/video/7690123456789012346','post_type':'images','description':'Photo'},
      {'url':'https://v16.tiktokcdn.com/media-file','description':'CDN-not-original'},
      {'url':post,'post_id':'7690123456789012346','description':'Mismatch'},
    ]
    with patch.object(bright_tiktok,'_request',side_effect=AssertionError('A provider call is prohibited in this test')):
        r=bright_tiktok._process_snapshot(job,records)
        assert r['valid_videos']==1 and r['provider_errors']==3,r
        assert r['status']=='ready_partial',r
        row=db.one('SELECT * FROM bt_jobs WHERE id=?',(job,))
        assert row['returned_records']==5 and row['provider_error_count']==3
        items=hot_radar.items(pid)
        assert len(items)==1 and items[0]['metadata']['media_url'].startswith('https://')
        assert items[0]['metadata']['metrics']['viewCount']==12500
        r2=bright_tiktok.restore_snapshot({'id':job})
        assert r2['from_cache'] and r2['imported']==0
        assert len(hot_radar.items(pid))==1
        assert len(bright_tiktok.job_errors(job))==3
        unknown='bd_unknown'
        db.execute('INSERT INTO bt_jobs(id,product_id,quantity,status,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                   (unknown,pid,10,'submission_unknown',time.time(),time.time()))
        x=bright_tiktok.attach_snapshot({'id':unknown,'snapshot_id':'recovered12345'})
        assert x['paid_calls']==0 and x['snapshot_id']=='recovered12345'
        try:bright_tiktok.attach_snapshot({'id':unknown,'snapshot_id':'another12345'})
        except ValueError:pass
        else:raise AssertionError('duplicate attach allowed')
    assert bright_e2e_media.valid_post_url(post)[1]=='7690123456789012345'
    for url in ('http://www.tiktok.com/@x/video/12345678901234567',
                'https://evil.tiktok.com.attacker.test/@x/video/12345678901234567',
                'https://www.tiktok.com/@x/photo/12345678901234567'):
        try:bright_e2e_media.valid_post_url(url)
        except ValueError:pass
        else:raise AssertionError('unsafe source accepted: '+url)
    # Real offline media proof: use FFmpeg and verify yt-dlp runner output can't point outside task folder.
    ff=media.ffmpeg_path()
    if ff and media.ffprobe_path():
        d=t/'fakepost';d.mkdir()
        dest=d/'original.mp4'
        command=[ff,'-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=black:s=192x320:r=4:d=3',
                 '-c:v','mpeg4','-q:v','5','-y',str(dest)]
        subprocess.run(command,check=True,timeout=30)
        def runner(cmd,timeout):
            return subprocess.CompletedProcess(cmd,0,stdout='7690123456789012345|||'+str(dest)+'\n',stderr='')
        info=bright_e2e_media.download_post(post,'7690123456789012345',d,runner=runner)
        assert info['bytes']>1500 and Path(info['path'])==dest
        sha=hashlib.sha256(dest.read_bytes()).hexdigest()
        aid='asset_testing'
        db.execute('''INSERT INTO assets(id,original_name,stored_name,kind,mime,path,sha256,size_bytes,duration,width,height,created_at)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                   (aid,'test.mp4','test.mp4','video','video/mp4',str(dest),sha,dest.stat().st_size,3,192,320,time.time()))
        item_id=items[0]['id']
        viral_review.bind({'item_id':item_id,'asset_id':aid})
        evidence=viral_review.visual_evidence(item_id,max_frames=12)
        assert len(evidence['frames'])>=5 and evidence['sha256']==sha
        dest.write_bytes(dest.read_bytes()+b'untrusted-edit')
        try:viral_review.visual_evidence(item_id,max_frames=12)
        except ValueError as ex:assert '哈希' in str(ex)
        else:raise AssertionError('tampering did not fail closed')
    print('INTEGRATED_BRIGHT_E2E_OFFLINE_TEST_PASS: errors, snapshot restore, no paid POST, media, hashes')
