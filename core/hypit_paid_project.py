"""Supervised production authorization for one statically bound TextVideo.

The Agent cannot issue a grant. Unknown or media-dependent plans are refused
until their exact wire request can be bound to a measured quote.
"""
import re
from xml.parsers import expat
from urllib.parse import urlsplit
import hashlib

from .hypit_cli_guard import validate_project_boundary
from .hypit_openlux_gateway import build_video_request, DurableVideoGateway
from .hypit_provider_bridge import OpenLuxHttpTransport, VerifiedRoute, ProviderLifecycle, SafeMp4Collector


def exact_text_request(folder):
    validate_project_boundary(folder)
    source = (folder/'main.svml').read_text(encoding='utf8')
    # Expat accepts lexical prefixes without requiring namespace declarations,
    # matching Hypit's own import vocabulary. No expressions are evaluated.
    source = re.sub(r'=\s*\{([A-Za-z_][A-Za-z0-9_.-]*)\}',lambda m:'="{'+m[1]+'}"',source)
    imports, texts, generations, stack = {},{},[],[]
    def start(tag,attrs):
        stack.append([tag,attrs,[]])
        if tag=='import' and 'as' in attrs and 'from' in attrs:
            imports[attrs['as']]=attrs['from']
        if ':' in tag and tag.split(':')[-1] in {'TextVideo','ReferenceVideo','FrameVideo'}:
            generations.append((tag,dict(attrs)))
    def data(value):
        if stack:stack[-1][2].append(value)
    def end(tag):
        node=stack.pop()
        if ':' in tag and imports.get(tag.split(':')[0])=='@hypit/text@1' and tag.endswith(':Value'):
            if 'id' in node[1]:texts[node[1]['id']]=''.join(node[2])
        if stack:stack[-1][2].extend(node[2])
    parser=expat.ParserCreate();parser.StartElementHandler=start;parser.EndElementHandler=end;parser.CharacterDataHandler=data
    parser.Parse(source,True)
    if len(generations)!=1:
        raise PermissionError('Paid builds currently require one exactly quoted generation request')
    tag,attrs=generations[0]
    if tag.split(':')[-1]!='TextVideo' or imports.get(tag.split(':')[0])!='@hypit/seedance@1':
        raise PermissionError('Media-reference pricing and transport require their own verified mapping')
    if attrs.get('model')!='2.5' or attrs.get('web-search','false')!='false':
        raise PermissionError('Only exact Seedance 2.5 without web search is verified')
    prompt=attrs.get('prompt','')
    match=re.fullmatch(r'\{([A-Za-z_][A-Za-z0-9_.-]*)\}',prompt)
    if not match or match[1] not in texts:
        raise PermissionError('Quote requires a local, static Text Value prompt')
    try:
        duration=int(attrs['duration'])
        audio=attrs.get('generate-audio','true')
        if audio not in {'true','false'}:raise ValueError('Boolean required')
        result=build_video_request(prompt=texts[match[1]],references=[],task_type='text',duration=duration,
            ratio=attrs.get('aspect-ratio','16:9'),resolution=attrs.get('resolution','720p'),generate_audio=audio=='true')
    except (KeyError,ValueError) as exc:
        raise PermissionError('Exact literal generation settings required for quote') from exc
    sizes={('720p','9:16'):(720,1280),('720p','16:9'):(1280,720),('720p','1:1'):(960,960),
           ('1080p','9:16'):(1080,1920),('1080p','16:9'):(1920,1080)}
    dimensions=sizes.get((result['resolution'],result['ratio']))
    if not dimensions:raise PermissionError('This output size has no verified account quote')
    return result,dimensions


def production_lifecycle(vault, quote, directory, video_probe):
    if hashlib.sha256(vault['openlux_api_key'].encode()).hexdigest()[:24]!=quote['account']:
        raise PermissionError('API key changed after quoting')
    transport=OpenLuxHttpTransport(vault['openlux_api_key'],VerifiedRoute(account_protocol_verified=True))
    gateway=DurableVideoGateway(directory/'video-gateway.db',transport,expected_account=quote['account'],verified_transport=True)
    class VendorCollector:
        def collect(self,url,destination):
            # This URL comes exclusively from an authenticated vendor response
            # with the exact acknowledged task identity, never from HTTP input.
            host=urlsplit(url).hostname
            return SafeMp4Collector({host},video_probe=video_probe).collect(url,destination)
    return ProviderLifecycle(gateway,VendorCollector(),backend_account_id=quote['account'])


def reference_source_spec(folder,inventory,*,task_type='reference'):
    """Static source-asset binding for the official ReferenceVideo Surface.

    Intermediate generated/transformed references need a separate local build
    and re-quote; they are never silently replaced by an original upload.
    """
    validate_project_boundary(folder)
    if task_type not in {'reference','edit','extend'}:raise PermissionError('Unsupported reference task type')
    source=(folder/'main.svml').read_text(encoding='utf8')
    source=re.sub(r'=\s*\{([A-Za-z_][A-Za-z0-9_.-]*)\}',lambda m:'="{'+m[1]+'}"',source)
    nodes=[];stack=[]
    def start(tag,attrs):
        node={'tag':tag,'attrs':attrs,'text':[],'children':[]};nodes.append(node)
        if stack:stack[-1]['children'].append(node)
        stack.append(node)
    def text(value):
        for node in stack:node['text'].append(value)
    parser=expat.ParserCreate();parser.StartElementHandler=start;parser.EndElementHandler=lambda tag:stack.pop();parser.CharacterDataHandler=text
    parser.Parse(source,True)
    imports={n['attrs'].get('as'):n['attrs'].get('from') for n in nodes if n['tag']=='import'}
    def module(node,name,tag):
        return node['tag'].endswith(':'+tag) and imports.get(node['tag'].split(':')[0])==name
    generations=[n for n in nodes if n['tag'].split(':')[-1] in {'ReferenceVideo','TextVideo','FrameVideo'}]
    if len(generations)!=1 or not module(generations[0],'@hypit/seedance@1','ReferenceVideo'):
        raise PermissionError('A single official ReferenceVideo is required')
    generation=generations[0];attrs=generation['attrs']
    texts={n['attrs'].get('id'):''.join(n['text']) for n in nodes if module(n,'@hypit/text@1','Value')}
    prompt_ref=re.fullmatch(r'\{([A-Za-z_][A-Za-z0-9_.-]*)\}',attrs.get('prompt',''))
    if not prompt_ref or prompt_ref[1] not in texts or attrs.get('model')!='2.5':
        raise PermissionError('Exact static Seedance 2.5 prompt required')
    assets={n['attrs'].get('id'):n for n in nodes if any(module(n,'@hypit/media@1',kind) for kind in ('Image','Video','Audio'))}
    selected={str((folder/i['path']).resolve()):i for i in inventory}
    references=[]
    for node in generation['children']:
        if node['tag'].split(':')[-1]!='Reference':raise PermissionError('Unknown reference child')
        choices=[k for k in ('image','video','audio') if k in node['attrs']]
        if len(choices)!=1:raise PermissionError('Exactly one reference role is required')
        kind=choices[0];value=node['attrs'][kind]
        ref=re.fullmatch(r'\{([A-Za-z_][A-Za-z0-9_-]*)\}',value)
        asset=assets.get(ref[1]) if ref else None
        if not asset or not module(asset,'@hypit/media@1',kind.title()):
            raise PermissionError('Reference must be a direct, measured local source artifact')
        file=(folder/asset['attrs'].get('src','')).resolve()
        bound=selected.get(str(file))
        if not bound or bound['kind']!=kind:raise PermissionError('Reference is outside the selected material inventory')
        person=node['attrs'].get('person-reference')
        if kind=='audio' and person is not None:raise PermissionError('Audio cannot have a person classification')
        if kind!='audio' and person!='false':raise PermissionError('Person-reference transport and permission remain unverified')
        references.append({'path':str(file),'sha256':bound['sha256'],'role':'reference_'+kind,
            'asset_id':bound['asset_id'],**({'personReference':False} if kind!='audio' else {})})
    if not 1<=len(references)<=16:raise PermissionError('Select 1-16 bounded references')
    references.sort(key=lambda ref:['reference_image','reference_video','reference_audio'].index(ref['role']))
    try:
        audio=attrs['generate-audio']
        if audio not in {'true','false'}:raise ValueError('Literal audio flag required')
        if attrs.get('web-search','false')!='false':raise ValueError('Reference web search unverified')
        result={'prompt':texts[prompt_ref[1]],'task_type':task_type,'duration':int(attrs['duration']),
                'ratio':attrs['aspect-ratio'],'resolution':attrs['resolution'],'generate_audio':audio=='true',
                'references':references}
    except (KeyError,ValueError) as exc:raise PermissionError('Explicit literal reference settings required') from exc
    return result
