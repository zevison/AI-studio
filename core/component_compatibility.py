"""Fail closed on a component catalog outside the host's reviewed interface contract."""
import re


def version(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d+\.\d+\.\d+',value):raise ValueError('组件兼容版本必须是三段数字')
    return tuple(map(int,value.split('.')))


def validate(key,item,host_version):
    if (item.get('id')!=key or item.get('os')!='windows' or item.get('architecture')!='x64'
        or item.get('rollback')!='versioned-retained'):
        raise PermissionError('组件身份、系统架构或回滚规则不兼容')
    if not version(item.get('app_minimum'))<=version(host_version)<version(item.get('app_maximum_exclusive')):
        raise PermissionError('组件与当前主程序版本不兼容')
    expected={'asr':('model-files','load-small-cpu-int8'), 'browser':('browser-zip','chrome-version')}[key]
    if (item.get('install_type'),item.get('health_check'))!=expected:
        raise PermissionError('组件安装或健康检查类型不受支持')
    allowed={'core.node','core.ffmpeg','core.hypit-sdk','core.asr-engine'}
    dependencies=item.get('dependencies')
    if not isinstance(dependencies,list) or not dependencies or set(dependencies)-allowed:
        raise PermissionError('组件依赖链包含未知模块')
    if type(item.get('installed_bytes')) is not int or not 0<item['installed_bytes']<2*1024**3:
        raise ValueError('组件磁盘大小无效')
