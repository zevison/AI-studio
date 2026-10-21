"""Official Hypit CLI integration for Seedance Studio.

This adapter invokes the PUBLISHED Hypit executable. It does not implement a
lookalike compiler. OpenLux paid video generation remains outside the Hypit
runtime until an authenticated Hypit-compatible OpenLux Provider is verified.

All creative project files, source video and CLI state live under data/, not
in the installer's replaceable program files. No CLI call can submit paid work
without an explicit approval event. UI can inspect and edit projects via Studio.
"""
from __future__ import annotations
import json, os, re, shutil, subprocess, threading, time, uuid, hashlib
from pathlib import Path
from . import config, db, media

ROOT = config.DATA_DIR / 'hypit_official'
ROOT.mkdir(parents=True, exist_ok=True)
LOCK = threading.RLock()
ACTIVE = set()
AUTO_ACTIVE = set()
SUPPORTED_VERSION = '0.3.1'  # verified reference target; never silently float latest
GITHUB_API = 'https://api.github.com/repos/hypit-ai/hypit/releases/latest'


def _hidden():
    return {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)} if os.name == 'nt' else {}


def _executable():
    override=os.environ.get('HYPIT_CLI','').strip()
    candidates = [override] if override else []
    candidates += [str(config.ROOT/'tools'/'hypit_runtime'/'node_modules'/'.bin'/('hypit.cmd' if os.name=='nt' else 'hypit'))]
    found=shutil.which('hypit')
    if found:candidates.append(found)
    for item in candidates:
        if item and Path(item).is_file():return item
    return None


def _run(args, cwd, timeout=35):
    exe=_executable()
    if not exe:raise RuntimeError('没有检测到官方 Hypit CLI，请先在「状态与设置」准备 Hypit 官方运行环境。')
    if not Path(cwd).is_dir():raise ValueError('Hypit 工程目录不存在')
    argv=[exe,*map(str,args)]
    # Never invoke via shell, never execute arbitrary command from browser.
    try:
        r=subprocess.run(argv, cwd=str(cwd), timeout=timeout, stdout=subprocess.PIPE,
             stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
             shell=False, **_hidden())
    except subprocess.TimeoutExpired as e:raise RuntimeError(f'官方 Hypit 执行超过 {timeout} 秒的安全等待上限') from e
    return {'ok':r.returncode==0,'code':r.returncode,
            'stdout':r.stdout[-14000:],'stderr':r.stderr[-10000:]}


def health():
    exe=_executable()
    status={'installed':bool(exe),'executable':exe or '', 'official_repository':'https://github.com/hypit-ai/hypit',
            'target_version':SUPPORTED_VERSION,'adapter':'official-cli','provider':'OpenLux paid request via Seedance firewall (external-to-runtime)',
            'can_build':bool(exe)}
    if exe:
        try:
            rr=_run(['--version'],config.ROOT,timeout=8)
            status['version_response']=(rr['stdout'] or rr['stderr']).strip()[:500]
            status['can_build']=rr['ok']
        except Exception as e:status['error']=str(e);status['can_build']=False
    return status


def _sha256(path):
    digest=hashlib.sha256()
    with open(path,'rb') as stream:
        for part in iter(lambda:stream.read(1024*1024),b''):
            digest.update(part)
    return digest.hexdigest()


def _id():return uuid.uuid4().hex[:14]


def _folder(pid):
    if not re.fullmatch(r'[a-f0-9]{14}',str(pid or '')):raise ValueError('无效 Hypit 工程 ID')
    p=ROOT/str(pid)
    if not p.is_dir():raise ValueError('Hypit 工程不存在')
    return p


def _save(path,obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp=path.with_name(path.name+'.tmp-'+_id())
    try:
        tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(tmp,path)
    finally:
        if tmp.exists():tmp.unlink()


def _record(pid):return json.loads((_folder(pid)/'project.json').read_text(encoding='utf-8'))


def _asset(aid, kind):
    row=db.one('SELECT * FROM assets WHERE id=?',(str(aid),))
    if not row or row.get('kind') not in kind:raise ValueError('素材不存在或素材类型不匹配')
    source=(config.ASSET_DIR/row['stored_name']).resolve()
    if not source.is_relative_to(config.ASSET_DIR.resolve()) or not source.is_file():raise ValueError('素材文件不可用')
    return row,source


def _copy_asset(folder, aid, kinds):
    row,source=_asset(aid,kinds)
    suffix=source.suffix.lower()
    if suffix not in ('.mp4','.mov','.mkv','.webm','.png','.jpg','.jpeg','.webp','.mp3','.wav','.aac','.m4a'):
        raise ValueError('Hypit 不支持此素材后缀')
    dest=folder/'assets'/('input_'+_sha256(source)[:14]+suffix)
    dest.parent.mkdir(parents=True,exist_ok=True)
    if not dest.exists() or _sha256(dest)!=_sha256(source):shutil.copy2(source,dest)
    return dest.relative_to(folder).as_posix()


def create_from_replica(replica_pid):
    """Prepare a real project folder and an agent brief; DOES NOT claim compiled workflow.

    Asset analysis and paid OpenLux generation stay on the existing proven path.
    When a completed clip exists, `attach_video` authors a REAL Hypit SVML
    project using that clip. Nothing is silently bought by this function.
    """
    from . import hybrid_workflow as hw
    existing=next((p for p in list_projects() if p.get('replica_project_id')==replica_pid),None)
    if existing:return existing
    replica=hw.project(replica_pid)
    simple=replica.get('simple') or {}
    if not simple:raise ValueError('不是可用的旧版复刻工程')
    _asset(simple.get('reference_asset_id'),('video',))
    folder=ROOT/_id();folder.mkdir(parents=True,exist_ok=False)
    try:
        video=_copy_asset(folder,simple['reference_asset_id'],('video',))
        pictures=[_copy_asset(folder,x,('image',)) for x in simple.get('product_asset_ids',[])[:30]]
        p={'id':folder.name,'replica_project_id':replica_pid,'name':replica.get('name','复刻作品'),
           'reference':video,'product_images':pictures,'instruction':simple.get('instruction',''),
           'status':'waiting_for_openlux','created_at':time.time(),'updated_at':time.time(),
           'official_build_id':None,'run':'build.svrun','output':None,'error':None}
        _save(folder/'project.json',p)
        (folder/'package.json').write_text(json.dumps({'name':'seedance-hypit-'+folder.name,'private':True,'hypit':{'project':True}},indent=2),encoding='utf-8')
        # Minimal self-contained LOCAL Runtime profile: no cloud accounts or phantom billing.
        _save(folder/'hypit.runtime.json',{'format':'hypit.runtime-local@1','dataRoot':'.hypit/runtimes/local',
             'credentials':{},'endpoints':{'media.local':{'use':'@hypit/media-local','config':{'defaultConcurrency':2}},
             'html.local':{'use':'@hypit/provider-html-local','config':{'workers':2,'quality':'standard','defaultConcurrency':1}}},'bindings':{}})
        note=(f'# Hypit 官方工作流创作说明\n\n参考视频：{video}\n商品素材：{", ".join(pictures)}\n'
              f'用户要求：{p["instruction"] or "保留参考视频的节奏与结构，替换商品素材。"}\n\n'
              '本项目必须使用官方 Hypit SVML/SVS/SVRun，不得使用模拟运行器。\n'
              'AI 镜头通过 Seedance Studio 的 OpenLux 付费确认生成，然后作为外部已批准素材进入官方 Hypit 工程。\n'
              '若需要更复杂的字幕、词锚点、B-roll 和效果，请使用官方 Hypit Skill + 能执行文件及命令的 Coding Agent 修改 main.svml；'
              '所有变更必须通过 hypit check、plan 验证后才能 build。\n')
        (folder/'AGENT_BRIEF.md').write_text(note,encoding='utf-8')
        return p
    except Exception:
        shutil.rmtree(folder,ignore_errors=True)
        raise


def _xml_safe(v):
    # Avoid introducing invalid expression syntax from a user's prompt.
    return (str(v).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
            .replace('{','(').replace('}',')'))


def _author(folder, video, duration):
    """Emit authentic SVML based on official Hypit Quickstart construction contracts.
    No mock Plan, fake runtime or undisclosed video API requests.
    """
    d=max(1.0,min(180.0,float(duration)))
    src=f'''<?svml using="@hypit/markup@1"?>
<svml>
<import as="time" from="@hypit/timeline-author@1"/>
<import as="space" from="@hypit/spatial@1"/>
<import as="media" from="@hypit/media@1"/>
<import as="text" from="@hypit/text@1"/>
<import as="seedance" from="@hypit/seedance@1"/>
<import as="mediaop" from="@hypit/media-operations@1"/>
<import as="visual" from="@hypit/visual-track@1"/>
<import as="audio" from="@hypit/audio-track@1"/>
<import as="film" from="@hypit/film@1"/>
<import as="html" from="@hypit/html-video@1"/>
<import as="recipes" source="./recipes.svs"/>
<time:Clock id="clock" frame-rate="24"/>
<text:Value id="approved-direction">Reproduce the approved OpenLux video take.</text:Value>
<seedance:TextVideo id="approved-take" model="2.5" prompt={{approved-direction}} duration="{15}" resolution="720p" aspect-ratio="9:16" generate-audio="true"/>
<mediaop:Normalize id="approved-media" source={{approved-take.video}}
 video="primary-moving" audio="default" span-authority="video" clock={{clock}}/>
<time:Timeline id="movie" clock={{clock}} end="picture.end">
 <time:Window id="picture" from="start" for={{approved-media.extent}}/>
</time:Timeline>
<space:Canvas id="canvas" width="720" height="1280"/>
<space:Frame id="fill" within={{canvas.bounds}} left="0%" top="0%" right="100%" bottom="100%"/>
<visual:Track id="video-track" timeline={{movie.timeline}}>
 <visual:Clip id="primary" media={{approved-media.media}} during={{movie.picture}} frame={{fill}} z="10" fit="contain"/>
</visual:Track>
<audio:Track id="audio-track" timeline={{movie.timeline}}>
 <audio:Clip id="primary" source={{approved-media.media}} during={{movie.picture}}/>
</audio:Track>
<film:Film id="film" canvas={{canvas.canvas}} timeline={{movie.timeline}} appearance={{recipes.film.main}}>
 <film:Track source={{video-track.visual}}/>
 <film:Track source={{audio-track.audio}}/>
</film:Film>
<html:Video id="final" composition={{film.composition}} timeline={{movie.timeline}}/>
</svml>
'''
    (folder/'main.svml').write_text(src,encoding='utf-8')
    (folder/'recipes.svs').write_text('<?svml using="@hypit/recipe@1"?>\n<sheet version="1" id="studio">\n film.main { background: #080B14; }\n</sheet>\n',encoding='utf-8')
    (folder/'build.svrun').write_text('<?svml using="@hypit/run-markup@1"?>\n<svrun version="1">\n <author source="./main.svml"/>\n <target output="final.video"/>\n <file id="approved-openlux" type="@hypit/artifact@1#BlobArtifact" from="./assets/approved_openlux.mp4" media-type="video/mp4"/>\n <satisfy output="approved-take.video" candidate="approved-openlux"/>\n</svrun>\n',encoding='utf-8')
    return {'source':str(folder/'main.svml'),'run':str(folder/'build.svrun')}


def attach_video(pid, video_path):
    folder=_folder(pid)
    with LOCK:
        p=_record(pid)
        if p.get('status') in ('building','completed') and (p.get('approved_video') or p.get('official_build_id')):
            return p
        source=Path(video_path).resolve()
        if not source.is_file():raise ValueError('OpenLux 视频尚未下载到本机')
        permitted=(config.OUTPUT_DIR.resolve(),config.DATA_DIR.resolve())
        if not any(source.is_relative_to(root) for root in permitted):raise ValueError('只允许复用工作台已经保存的生成视频')
        dest=folder/'assets'/'approved_openlux.mp4'
        if not dest.exists() or _sha256(dest)!=_sha256(source):
            staged=dest.with_name('approved_openlux.'+_id()+'.mp4')
            try:
                shutil.copy2(source,staged)
                os.replace(staged,dest)
            finally:
                if staged.exists():staged.unlink()
        # A completed FREE test is proof only for the exact tested sources.
        # It is not proof that a later paid artifact has been compiled.
        p.pop('preflight',None)
        try:duration=float(media.probe(dest).get('duration') or 0)
        except Exception:duration=0
        if duration<.5:raise ValueError('视频时长不可读取；不能交给 Hypit 构建')
        _author(folder,'./assets/approved_openlux.mp4',duration)
        p.update(status='ready_to_plan',approved_video=dest.name,updated_at=time.time(),error=None)
        _save(folder/'project.json',p)
        return p



# 【注意/Risk】Free preflight uses the real Hypit CLI with a locally generated
# 2-second fixture. No OpenLux/provider video-generation request is permitted.
# Any unknown pricing / malformed plan / CLI ambiguity FAILS CLOSED.
# Actual Windows Hypit CLI rendering must still be verified on the target PC.
def _local_only_pricing(folder):
    priced=_run(['pricing','build.svrun','--json'],folder,timeout=60)
    if not priced['ok']:
        raise RuntimeError('官方 Hypit 费用审查失败（没有提交构建）：'+(priced['stderr'] or priced['stdout'])[:450])
    try:
        data=json.loads(priced['stdout'].strip())
    except (TypeError,ValueError) as exc:
        raise RuntimeError('Hypit 费用报告不是可验证的 JSON，已禁止执行真实构建') from exc
    if not isinstance(data,dict) or not isinstance(data.get('groups'),list):
        raise RuntimeError('Hypit 费用报告字段不兼容，必须先人工核实当前官方版本')
    for group in data['groups']:
        if not isinstance(group,dict):
            raise RuntimeError('Hypit 费用报告内容异常，已阻止构建')
        requests=group.get('requests',[])
        if not isinstance(requests,list):
            raise RuntimeError('Hypit 费用报告缺少可验证请求清单')
        if not requests:
            continue
        # Only explicit LOCAL no-charge entries may be accepted, and only if
        # the provider/endpoint ownership is visible in structured metadata.
        meta=' '.join(str(group.get(k,'')).lower() for k in ('provider','endpoint','service','status','priceStatus','pricingStatus','chargeType','billingMode'))
        if ('local' not in meta or any(term in meta for term in ('unknown','paid','remote','external','hypihub','openlux'))):
            raise RuntimeError('Hypit 计划含有未能证实免费且本地执行的请求；为保护费用已阻止构建')
    return True


def _preflight_fingerprint(pid, workflow_version_id, folder):
    exe=_executable()
    if not exe:raise ValueError('未安装官方 Hypit，禁止付费')
    p=_record(pid)
    if p.get('replica_project_id') is None:raise ValueError('Hypit 工程没有绑定复刻任务')
    material={
        'replica_project_id':p['replica_project_id'],
        'workflow_version_id':str(workflow_version_id),
        'reference':_sha256(folder/p['reference']),
        'product_images':[ _sha256(folder/item) for item in p.get('product_images',[]) ],
        'instruction':p.get('instruction',''),
        'hypit_cli':_sha256(Path(exe)),
        'main.svml':_sha256(folder/'main.svml'),
        'build.svrun':_sha256(folder/'build.svrun'),
        'recipes.svs':_sha256(folder/'recipes.svs'),
        'runtime_profile':_sha256(folder/'hypit.runtime.json'),
    }
    return hashlib.sha256(json.dumps(material,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()


def _local_fixture(folder, source):
    # Use a two-second portion of the user's OWN reference video. This is a
    # real MP4, not a dummy or a fake success flag; no model request occurs.
    import subprocess
    target=folder/'assets'/'approved_openlux.mp4'
    if target.exists() and target.stat().st_size>1024:
        try:
            if float(media.probe(target).get('duration') or 0)>0.4:return target
        except (OSError,ValueError,RuntimeError):pass
        target.unlink()
    binary=str(media.ffmpeg_path() or '')
    if not binary or not Path(binary).is_file():
        found=shutil.which('ffmpeg')
        if not found:raise RuntimeError('未检测到 FFmpeg，无法免费制作 Hypit 验证素材')
        binary=found
    target.parent.mkdir(parents=True,exist_ok=True)
    tmp=target.with_name('preflight_fixture_'+_id()+'.mp4')
    args=[binary,'-hide_banner','-loglevel','error','-nostdin','-y','-i',str(source),'-t','2',
          '-map','0:v:0','-map','0:a:0?','-c:v','libx264','-preset','ultrafast',
          '-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(tmp)]
    try:
        r=subprocess.run(args,cwd=str(folder),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE,timeout=100,shell=False,**_hidden())
        if r.returncode or not tmp.is_file() or tmp.stat().st_size<1024:
            raise RuntimeError('无法用参考视频制作本地 MP4 测试素材：'+r.stderr.decode('utf-8','replace')[-500:])
        if float(media.probe(tmp).get('duration') or 0)<0.5:
            raise RuntimeError('免费验证素材时长不可读取')
        os.replace(tmp,target)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('FFmpeg 免费验证素材制作超时；没有调用付费接口') from exc
    finally:
        if tmp.exists():tmp.unlink()
    return target


def preflight(replica_pid, workflow_version_id):
    """Official local compile+plan+build+export BEFORE any OpenLux charge.

    Idempotent per source hashes; no provider request, and no silent fallback.
    If an official build is ambiguous, never resubmit it automatically.
    """
    from . import hybrid_workflow as hw
    project=hw.project(replica_pid)
    simple=project.get('simple') or {}
    if str(simple.get('workflow_version_id') or '')!=str(workflow_version_id or ''):
        raise ValueError('当前复刻项目与已确认工作流不一致，拒绝付费')
    with LOCK:
        entry=create_from_replica(replica_pid)
        pid=entry['id']; folder=_folder(pid)
        if entry.get('status') in ('building','submitted','build_unknown','completed'):
            raise ValueError('已有 Hypit 构建记录，不能重新执行免费验证并覆盖结果')
        source=folder/entry['reference']
        _local_fixture(folder,source)
        _author(folder,'./assets/approved_openlux.mp4',2)
        digest=_preflight_fingerprint(pid,workflow_version_id,folder)
        saved=entry.get('preflight') or {}
        output=folder/'preflight_output.mp4'
        if saved.get('verified') and saved.get('digest')==digest and output.is_file() and output.stat().st_size>1024:
            if saved.get('output_sha256')==_sha256(output) and float(media.probe(output).get('duration') or 0)>.4:
                return {'verified':True,'project_id':pid,'digest':digest,'reused':True,'method':'official-hypit-local-build'}
        # Never reuse stale evidence or re-submit a build with unknown outcome.
        if saved.get('submitted_build_id') or saved.get('submission_unknown'):
            raise ValueError('上次免费 Hypit Build 状态尚未完全核实，禁止重复提交')
        entry.pop('preflight',None)
        entry.update(status='preflighting',updated_at=time.time())
        _save(folder/'project.json',entry)
        try:
            rr=_run(['runtime','use','hypit.runtime.json'],folder,timeout=30)
            if not rr['ok']:raise RuntimeError('Hypit Runtime 选择失败：'+(rr['stderr'] or rr['stdout'])[:600])
            check_res=_run(['check','main.svml'],folder,timeout=90)
            if not check_res['ok']:raise RuntimeError('付费前 Hypit 源码检查失败：'+(check_res['stderr'] or check_res['stdout'])[:800])
            warm=_run(['runtime','up'],folder,timeout=240)
            if not warm['ok']:raise RuntimeError('付费前 Hypit 本地 Runtime 准备失败：'+(warm['stderr'] or warm['stdout'])[:800])
            plan_res=_run(['plan','build.svrun'],folder,timeout=90)
            if not plan_res['ok']:raise RuntimeError('付费前 Hypit Plan 未通过：'+(plan_res['stderr'] or plan_res['stdout'])[:900])
            # Structured price inspection must happen BEFORE official build.
            _local_only_pricing(folder)
            entry['preflight']={'verified':False,'digest':digest,'submission_unknown':True,
                                'created_at':time.time()}
            _save(folder/'project.json',entry)
            # This is the ACTUAL official local Build, using the free fixture.
            # Build is never automatically retried if result is uncertain.
            built=_run(['build','build.svrun','--follow'],folder,timeout=240)
            raw=(built['stdout']+'\n'+built['stderr'])
            found=re.search(r'\bbld_[0-9A-Za-z_\-]+',raw)
            if found:
                entry['preflight']['submitted_build_id']=found.group(0)
                _save(folder/'project.json',entry)
            if not built['ok'] or not found:
                raise RuntimeError('本地 Hypit Build 未确认成功，禁止再次提交：'+raw[-950:])
            target=folder/'preflight_output.mp4'
            got=_run(['get',found.group(0),'--output','final.video','--to',str(target)],folder,timeout=240)
            if not got['ok'] or not target.is_file() or target.stat().st_size<1024:
                raise RuntimeError('Hypit 免费成片导出失败，付费已被阻止：'+(got['stderr'] or got['stdout'])[-750:])
            if float(media.probe(target).get('duration') or 0)<0.4:
                raise RuntimeError('Hypit 免费成片无法解码，已阻止付费')
            entry['preflight']={'verified':True,'digest':digest,'output_sha256':_sha256(target),
                                'submitted_build_id':found.group(0),'created_at':time.time()}
            entry.update(status='waiting_for_openlux',error=None,updated_at=time.time())
            _save(folder/'project.json',entry)
            return {'verified':True,'project_id':pid,'digest':digest,'reused':False,'method':'official-hypit-local-build'}
        except Exception as exc:
            entry['status']='preflight_blocked'
            entry['error']=str(exc)[:1000]
            entry['updated_at']=time.time()
            _save(folder/'project.json',entry)
            raise


def require_paid_ready(replica_pid, workflow_version_id, expected_digest):
    """Cheap mandatory SERVER-SIDE seal check just before paid request."""
    p=original_project_for_replica(replica_pid)
    if not p:raise ValueError('没有执行官方 Hypit 免费验证，禁止付费')
    folder=_folder(p['id'])
    evidence=p.get('preflight') or {}
    if p.get('status')!='waiting_for_openlux' or not evidence.get('verified'):
        raise ValueError('官方 Hypit 免费完整构建未通过，禁止付费')
    if not expected_digest or evidence.get('digest')!=expected_digest:
        raise ValueError('预览令牌与 Hypit 验证记录不一致，禁止付费')
    # Ensure a different edited project/version cannot borrow another task's seal.
    h=_preflight_fingerprint(p['id'],workflow_version_id,folder)
    if h!=expected_digest:raise ValueError('Hypit 工程或输入素材已改变，请重新免费验证；未付费')
    target=folder/'preflight_output.mp4'
    if not target.is_file() or _sha256(target)!=evidence.get('output_sha256'):
        raise ValueError('Hypit 本地测试成片或证据不完整，禁止付费')
    return p

def list_projects():
    out=[]
    for q in ROOT.iterdir():
        if not q.is_dir() or not re.fullmatch('[a-f0-9]{14}',q.name):continue
        try:out.append(json.loads((q/'project.json').read_text(encoding='utf-8')))
        except Exception:pass
    return sorted(out,key=lambda x:x.get('updated_at',0),reverse=True)


def get_project(pid):
    p=_record(pid)
    p['official_installed']=bool(_executable())
    return p


def prepare(pid):
    folder=_folder(pid)
    rr=_run(['runtime','use','hypit.runtime.json'],folder,timeout=30)
    if not rr['ok']:raise RuntimeError('官方 Hypit Runtime 未选择成功：'+rr['stderr'][:500])
    return rr


def check(pid):
    folder=_folder(pid)
    if not (folder/'main.svml').is_file():raise ValueError('等待 OpenLux 成片或官方 Agent 写入真正的 SVML 工程')
    r=_run(['check','main.svml'],folder,timeout=60)
    return r


def plan(pid):
    folder=_folder(pid)
    if not (folder/'build.svrun').is_file():raise ValueError('当前 Hypit 工程尚未生成 build.svrun')
    r=_run(['plan','build.svrun'],folder,timeout=90)
    return r


def _submit_worker(pid):
    folder=_folder(pid)
    try:
        r=_run(['runtime','use','hypit.runtime.json'],folder,timeout=25)
        if not r['ok']:raise RuntimeError('Runtime 选择失败：'+r['stderr'][:400])
        # Do NOT run runtime up automatically: it may download large programs.
        r=_run(['plan','build.svrun'],folder,timeout=80)
        if not r['ok']:raise RuntimeError('官方 Hypit 计划校验失败：'+r['stderr'][:850])
        # Final server-side check: Hypit must not issue ANY unapproved provider call.
        _local_only_pricing(folder)
        # hypit build creates one durable official Build. On timeout NEVER resubmit.
        r=_run(['build','build.svrun'],folder,timeout=120)
        p=_record(pid)
        if not r['ok']:
            p.update(status='build_unknown',error='官方 Build 返回异常，请使用 hypit builds/status 核实，禁止自动重交：'+r['stderr'][:800])
        else:
            m=re.search(r'\bbld_[0-9A-Za-z_\-]+',r['stdout']+'\n'+r['stderr'])
            p.update(status='submitted',official_build_id=m.group(0) if m else None,
                     error=None if m else '提交可能已成功，但未识别 Build ID；请从官方 Builds 中核对。')
        p['updated_at']=time.time();_save(folder/'project.json',p)
    except Exception as e:
        p=_record(pid);p.update(status='build_unknown',error=str(e)[:1200],updated_at=time.time());_save(folder/'project.json',p)
    finally:
        with LOCK:ACTIVE.discard(pid)


def submit(pid, approved=False):
    if approved is not True:raise ValueError('需要明确的执行确认；本接口不自动发起任何付费模型调用')
    folder=_folder(pid)
    with LOCK:
        p=_record(pid)
        if pid in ACTIVE or p.get('status') in ('building','submitted','completed','build_unknown'):
            raise ValueError('已有官方 Hypit Build 或结果未核实，阻止重复提交')
        if not (folder/'build.svrun').exists():raise ValueError('当前工程尚无可执行 Hypit Run Source')
        p.update(status='building',updated_at=time.time());_save(folder/'project.json',p)
        ACTIVE.add(pid)
        threading.Thread(target=_submit_worker,args=(pid,),daemon=True,name='hypit-official-'+pid).start()
    return p


def refresh(pid):
    p=_record(pid); bid=p.get('official_build_id')
    if not bid:return p
    r=_run(['status',bid],_folder(pid),timeout=20)
    p['latest_status']=(r['stdout']+'\n'+r['stderr'])[-2500:]
    if re.search(r'\b(completed|succeeded|success)\b',p['latest_status'],re.I):
        p['status']='completed'
    elif re.search(r'\b(failed|cancelled|canceled)\b',p['latest_status'],re.I):
        p['status']='failed'
    p['updated_at']=time.time();_save(_folder(pid)/'project.json',p)
    return p


def export(pid):
    p=_record(pid)
    bid=p.get('official_build_id')
    if not bid:raise ValueError('官方 Build ID 不存在，无法导出')
    target=_folder(pid)/'output'/'final.mp4';target.parent.mkdir(exist_ok=True)
    r=_run(['get',bid,'--output','final.video','--to',str(target)],_folder(pid),timeout=240)
    if not r['ok'] or not target.is_file():raise RuntimeError('官方 Hypit 成片导出失败：'+r['stderr'][:700])
    dst=config.OUTPUT_DIR/f'hypit_{pid}.mp4';shutil.copy2(target,dst)
    p.update(status='completed',output='/outputs/'+dst.name,error=None,updated_at=time.time())
    _save(_folder(pid)/'project.json',p)
    return p


def official_update_check():
    # Read-only, no installers, no network when invoked during startup.
    import urllib.request
    try:
        req=urllib.request.Request(GITHUB_API,headers={'User-Agent':'SeedanceStudio-hypit-official-update-check','Accept':'application/vnd.github+json'})
        with urllib.request.urlopen(req,timeout=8) as f:obj=json.load(f)
        return {'ok':True,'latest':str(obj.get('tag_name') or ''),'url':str(obj.get('html_url') or ''),
                'current':health().get('version_response',''),'requires_compatibility_test':True,
                'auto_installed':False}
    except Exception as e:return {'ok':False,'reason':str(e)[:250],'auto_installed':False}


def original_project_for_replica(replica_pid):
    return next((p for p in list_projects() if p.get('replica_project_id')==replica_pid),None)


def _native_loop(pid):
    """Resume-safe observer. No model calls are ever made by this loop."""
    try:
        p=_record(pid)
        if p.get('status')=='ready_to_plan':
            if not _executable():return
            prepare(pid)
            cp=check(pid)
            if not cp['ok']:
                p.update(status='source_invalid',error='官方 SVML 检查未通过：'+cp['stderr'][:900]);_save(_folder(pid)/'project.json',p)
                return
            ready=_run(['runtime','up'],_folder(pid),timeout=240)
            if not ready['ok']:
                p.update(status='needs_runtime',error='官方 Hypit 本地依赖准备未完成：'+ready['stderr'][:900]);_save(_folder(pid)/'project.json',p)
                return
            plan_data=plan(pid)
            if not plan_data['ok']:
                p.update(status='source_invalid',error='官方 Hypit Plan 未通过：'+plan_data['stderr'][:900]);_save(_folder(pid)/'project.json',p)
                return
            # After the paid MP4 arrives, Hypit MUST still only reuse the approved
            # asset. A textual Plan keyword search cannot prove this safety.
            # Real structured pricing must prove every demanded request local/free.
            try:
                _local_only_pricing(_folder(pid))
            except RuntimeError as exc:
                p.update(status='manual_review',error='官方 Hypit 构建费用审查失败：'+str(exc)[:850],updated_at=time.time())
                _save(_folder(pid)/'project.json',p)
                return
            submit(pid,approved=True)
            return
        if p.get('status')=='submitted' and p.get('official_build_id'):
            r=refresh(pid)
            if r.get('status')=='completed' and not r.get('output'):
                try:export(pid)
                except Exception as e:
                    r.update(status='export_error',error=str(e)[:900]);_save(_folder(pid)/'project.json',r)
    except Exception as e:
        p=_record(pid);p.update(status='source_invalid',error=str(e)[:900],updated_at=time.time());_save(_folder(pid)/'project.json',p)


def auto_resume(replica_pid, generated_video=None):
    """Advance safely from OpenLux artifact to official build; thread once per project."""
    p=original_project_for_replica(replica_pid)
    if not p and not generated_video:return None
    if not p:p=create_from_replica(replica_pid)
    pid=p['id']
    if generated_video and p.get('status')=='waiting_for_openlux':p=attach_video(pid,generated_video)
    if p.get('status') not in ('ready_to_plan','submitted'):return p
    if not health().get('can_build'):return p
    with LOCK:
        if pid in AUTO_ACTIVE:return p
        AUTO_ACTIVE.add(pid)
    def safe():
        try:_native_loop(pid)
        finally:
            with LOCK:AUTO_ACTIVE.discard(pid)
    threading.Thread(target=safe,daemon=True,name='hypit-auto-'+pid).start()
    return p


def studio_start(pid):
    folder=_folder(pid)
    if not (folder/'build.svrun').is_file():raise ValueError('尚无可编辑的 Hypit 工程')
    if not _executable():raise ValueError('未安装官方 Hypit CLI')
    cmd=[_executable(),'studio','--run','build.svrun','--port','5179']
    kwargs=_hidden()
    kwargs['stdout']=subprocess.DEVNULL;kwargs['stderr']=subprocess.DEVNULL
    subprocess.Popen(cmd,cwd=str(folder),shell=False,stdin=subprocess.DEVNULL,**kwargs)
    return {'url':'http://127.0.0.1:5179','note':'已请求启动官方 Hypit Studio。首次打开可能需要稍候。'}


def setup_state():
    log=config.DATA_DIR/'hypit_official_install.log'
    flag=config.DATA_DIR/'hypit_official_install.state'
    latest=flag.read_text(encoding='utf-8',errors='replace').strip() if flag.exists() else ''
    return {'installed':health().get('can_build',False),'state':latest or 'not_started',
            'log_tail':log.read_text(encoding='utf-8',errors='replace')[-2200:] if log.exists() else ''}


def setup_start():
    if os.name!='nt':raise ValueError('自动准备仅支持 Windows；其他系统请按 Hypit 官方文档安装 CLI')
    s=setup_state()
    if s['state']=='running':raise ValueError('Hypit 环境正在准备，请稍候')
    script=config.ROOT/'tools'/'setup_hypit_official.ps1'
    if not script.is_file():raise ValueError('缺少 Hypit 官方安装脚本，请重新安装累积补丁')
    flag=config.DATA_DIR/'hypit_official_install.state'
    flag.write_text('running',encoding='utf-8')
    cmd=['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),'-AppRoot',str(config.ROOT)]
    try:
        subprocess.Popen(cmd,cwd=str(config.ROOT),stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,shell=False,**_hidden())
    except Exception:
        flag.write_text('failed',encoding='utf-8');raise
    return {'started':True,'status':'running','note':'安装官方 Hypit CLI 和本地依赖可能需要下载，请在设置页面查看实时日志。'}
