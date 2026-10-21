"""Execute only verified installed component bytes, outside the UI/startup path."""
import os
from pathlib import Path
import subprocess
import time
import json
import sys
import uuid


def check(root,key,folder,item):
    started=time.monotonic()
    if key=='asr':
        from .asr_model import cpu_supported
        if not cpu_supported():raise RuntimeError('当前 CPU 不支持 AVX2，本地语音组件不可用；其他功能仍可使用')
        from faster_whisper import WhisperModel
        from .asr_model import load_local_model
        model=load_local_model(WhisperModel,folder)
        if not model.model.is_multilingual:raise RuntimeError('本地语音模型不符合多语言 small 规格')
        del model
        details={'engine':'faster-whisper','device':'cpu','compute_type':'int8','model_loaded':True}
    elif key=='browser':
        binary=Path(folder)/item['cache_root']/'chrome-headless-shell-win64/chrome-headless-shell.exe'
        environment={k:v for k,v in os.environ.items() if not any(x in k.upper() for x in ('KEY','TOKEN','SECRET','PASSWORD'))}
        result=subprocess.run([str(binary),'--version'],capture_output=True,timeout=20,env=environment,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        output=result.stdout.decode('utf8','replace').strip()
        if result.returncode or item['version'] not in output:raise RuntimeError('视频合成浏览器未通过真实启动和版本检查')
        details={'executable_started':True,'version_output':output}
    else:raise ValueError('未知组件健康检查')
    return {'available':True,'checked_at':time.time(),'seconds':round(time.monotonic()-started,3),'details':details}


def check_in_subprocess(root,home,key,folder,item):
    """Native DLL crashes/timeouts cannot terminate the application backend."""
    from .component_manager import plain,identity
    directory=plain(Path(home)/'components/health');directory.mkdir(parents=True,exist_ok=True)
    report=directory/(uuid.uuid4().hex+'.json')
    prefix=[sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(Path(root)/'desktop/launcher.py')]
    command=[*prefix,'--component-health',key,'--component-directory',str(folder),
             '--health-report',str(report),'--data-home',str(home)]
    from .component_updates import proof_for
    from .component_manager import embedded_catalog
    original=embedded_catalog(root).get(key)
    if original and identity(original)==identity(item):command+=['--component-baseline']
    else:
        proof_id=proof_for(root,home,key,item)
        if not proof_id:raise PermissionError('组件候选缺少受信任的签名证明')
        command+=['--component-proof',proof_id]
    environment={k:v for k,v in os.environ.items() if not any(x in k.upper() for x in ('KEY','TOKEN','SECRET','PASSWORD'))
                 and not k.upper().startswith(('STUDIO_','PYTHON','OPENLUX_','BRIGHTDATA_','OSS_'))}
    if not getattr(sys,'frozen',False):environment['PYTHONPATH']=str(Path(root).resolve())
    result=subprocess.run(command,capture_output=True,stdin=subprocess.DEVNULL,timeout=120,env=environment,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if not report.is_file():raise RuntimeError('组件检测进程未返回结果（退出码 '+str(result.returncode)+'），主程序保持运行')
    if report.stat().st_size>65536:raise ValueError('组件检测报告超限')
    value=json.loads(report.read_text('utf8'))
    if (value.get('format')!='seedance.component-health@1' or value.get('id')!=key
        or value.get('identity')!=identity(item)):raise PermissionError('组件检测报告身份不匹配')
    if result.returncode or not value.get('available'):raise RuntimeError(value.get('reason') or '组件未通过独立进程检测')
    return value


def worker(root,home,key,folder,report,proof_id=None,baseline=False):
    from .component_manager import plain,catalog,identity,validate_installed
    import re
    folder,report=plain(folder),plain(report)
    base=plain(Path(home)/'components')
    if (not folder.is_relative_to(base/key) or report.parent!=base/'health'
        or not re.fullmatch(r'[a-f0-9]{32}\.json',report.name) or report.exists()):
        raise PermissionError('组件检测路径超出管理目录')
    if baseline:
        from .component_manager import embedded_catalog
        item=embedded_catalog(root).get(key)
    elif proof_id:
        from .component_updates import load_proof
        item=load_proof(root,home,key,proof_id)[0]
    else:item=catalog(root,home=home).get(key)
    if not item or not validate_installed(folder,item):raise PermissionError('组件内容未通过受信任清单校验')
    value={'format':'seedance.component-health@1','id':key,'identity':identity(item)}
    try:value.update(check(root,key,folder,item));code=0
    except Exception as exc:value.update(available=False,reason=str(exc)[:400]);code=1
    with report.open('x',encoding='utf8') as stream:json.dump(value,stream,ensure_ascii=False)
    return code
