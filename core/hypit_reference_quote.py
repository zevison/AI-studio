"""Measured, role-preserving reference quote preparation with explicit upload consent."""
from decimal import Decimal
from pathlib import Path
from .hypit_paid_project import reference_source_spec
from .hypit_openlux_gateway import build_video_request,request_sha256
from .hypit_output_policy import sha
from .hypit_video_quote import quote_video


def prepare_reference_quote(folder,inventory,vault,*,task_type,limit_usd,upload,normalize,probe,quote_reader=quote_video):
    spec=reference_source_spec(folder,inventory,task_type=task_type)
    assets=[]
    for ref in spec['references']:
        file=Path(ref['path'])
        if sha(file)!=ref['sha256']:raise PermissionError('Reference source changed')
        assets.append({'role':ref['role'],'asset':{'id':ref['asset_id'],'path':str(file),
            'sha256':ref['sha256'],'kind':ref['role'].removeprefix('reference_'),
            'stored_name':file.name,'original_name':file.name,'size_bytes':file.stat().st_size}})
    prepared=normalize(assets)
    inputs=prepared['specs']
    if len(inputs)!=len(assets):raise PermissionError('Local preparation changed reference cardinality')
    seconds=Decimal(0);audio_seconds=Decimal(0);total=0;video=[]
    for original,item in zip(assets,inputs):
        if original['role']!=item['role']:raise PermissionError('Local preparation changed a reference role')
        asset=item['asset'];file=Path(asset['path'])
        total+=file.stat().st_size
        if total>1024**3 or file.stat().st_size>400*1024**2 or sha(file)!=asset['sha256']:
            raise PermissionError('Prepared reference is changed or exceeds byte bounds')
        if item['role'] in {'reference_video','reference_audio'}:
            info=probe(file)
            duration=Decimal(str(info.get('duration') or 0))
            minimum=4 if task_type=='edit' and item['role']=='reference_video' else 2
            if not info.get('ok') or not minimum<=duration<=30:raise PermissionError('Reference duration is outside verified model bounds')
            if item['role']=='reference_video':
                if (file.suffix.lower() not in {'.mp4','.mov'} or file.stat().st_size>200*1024**2
                    or info.get('video_codec') not in {'h264','hevc'}
                    or not 24<=float(info.get('fps') or 0)<=60
                    or not 407696<=int(info.get('width') or 0)*int(info.get('height') or 0)<=8295044):
                    raise PermissionError('Reference video container, codec, frame rate, pixels or size is unsupported')
                allowed_audio={'aac','mp3'} if file.suffix.lower()=='.mp4' else {'aac','mp3','pcm_s16le','pcm_s24le','pcm_s32le'}
                if info.get('has_audio') and info.get('audio_codec') not in allowed_audio:
                    raise PermissionError('Reference video audio codec is unsupported')
                seconds+=duration;video.append(info)
            else:audio_seconds+=duration
        elif item['role']=='reference_image':
            from PIL import Image
            if file.stat().st_size>=30*1024**2:
                raise PermissionError('Reference image exceeds model byte limit')
            with Image.open(file) as image:
                w,h=image.size
                if not (300<=w<=6000 and 300<=h<=6000 and .4<=w/h<=2.5):
                    raise PermissionError('Reference image dimensions or aspect ratio are unsupported')
                image.verify()
    if seconds>30 or audio_seconds>30:raise PermissionError('Reference audio/video duration exceeds 30 seconds')
    ratios={'9:16':(720,1280),'16:9':(1280,720),'1:1':(960,960)}
    ratio=spec['ratio']
    if ratio=='adaptive':
        if len(video)!=1:raise PermissionError('Adaptive quote requires one measured video source')
        info=video[0]
        candidates=[r for r,(w,h) in ratios.items() if abs(float(info['width'])/float(info['height'])-w/h)<.002]
        if len(candidates)!=1:raise PermissionError('This adaptive aspect ratio has no verified account pixel quote')
        ratio=candidates[0]
    dimensions=ratios.get(ratio)
    if spec['resolution']=='1080p':dimensions={'9:16':(1080,1920),'16:9':(1920,1080)}.get(ratio)
    elif spec['resolution']!='720p':dimensions=None
    if not dimensions:raise PermissionError('Reference output dimensions are not verified')
    arguments={k:spec[k] for k in ('prompt','task_type','duration','ratio','resolution','generate_audio')}
    placeholders=[{'role':item['role'],'url':f'https://preflight.example.invalid/{n}'} for n,item in enumerate(inputs)]
    payload=build_video_request(**arguments,references=placeholders)
    quote=quote_reader(vault,payload,width=dimensions[0],height=dimensions[1],input_video_seconds=seconds)
    if Decimal(quote.get('planning_reserve_usd',quote['estimated_usd']))>Decimal(str(limit_usd)):
        raise PermissionError('Reference quote plus planning reserve exceeds upload/build budget; no upload made')
    manifest=[];wire=[];uploaded={}
    for original,item in zip(spec['references'],inputs):
        asset=item['asset']
        if asset['sha256'] not in uploaded:uploaded[asset['sha256']]=upload(asset)
        receipt=uploaded[asset['sha256']]
        if receipt['sha256']!=asset['sha256']:raise PermissionError('Upload receipt content changed')
        wire.append({'role':item['role'],'url':receipt['url']})
        manifest.append({'sha256':original['sha256'],'role':original['role'],'url':receipt['url'],
            'uploaded_sha256':asset['sha256'],'object_key':receipt['object_key'],'etag':receipt['etag'],
            'version_id':receipt.get('version_id'),**({'personReference':False} if original['role']!='reference_audio' else {})})
    payload=build_video_request(**arguments,references=wire)
    verified_edit=(task_type=='edit' and len(inputs)==1 and inputs[0]['role']=='reference_video'
        and spec['duration']==-1 and spec['ratio']=='adaptive' and spec['resolution']=='720p'
        and spec['generate_audio'] is False)
    quote.update(request_hash=request_sha256(payload),task_type=task_type,
        reference_count=len(wire),reference_protocol_live_verified=verified_edit,
        reference_live_verified_scope='single non-person video edit; adaptive; 720p; no generated audio' if verified_edit else None,
        protocol_sources=['https://docs.volcengine.com/docs/ark/seedance-2-5?lang=zh'],
        reference_normalization=prepared.get('fixes') or [])
    return payload,quote,manifest
