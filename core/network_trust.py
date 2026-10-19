"""Keep system/enterprise roots and supplement them with the pinned CA bundle."""
from functools import lru_cache
import ssl
import socket


@lru_cache(maxsize=1)
def tls_context():
    context=ssl.create_default_context()
    try:
        import certifi
        context.load_verify_locations(cafile=certifi.where())
    except ImportError:
        pass
    return context


def network_error_message(error):
    cause=getattr(error,'reason',error)
    if isinstance(cause,ssl.SSLCertVerificationError):
        return 'HTTPS 证书校验失败。请检查系统时间、系统根证书和代理证书；程序不会关闭证书验证。'
    if isinstance(cause,(TimeoutError,socket.timeout)):
        return '网络等待超时。请检查网络或代理；已提交的生成请求不会自动重发。'
    if isinstance(cause,socket.gaierror):
        return '无法解析服务地址，请检查网络、DNS 和代理设置。'
    if isinstance(cause,ConnectionRefusedError):
        return '服务或代理拒绝连接，请检查接口地址和代理是否正在运行。'
    return '网络连接失败（'+type(cause).__name__+'），请检查接口地址、网络及代理。'
