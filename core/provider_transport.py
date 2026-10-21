"""Credential-bearing HTTPS with public-IP pinning and no redirects/proxies."""
from __future__ import annotations
import http.client
import ipaddress
import socket
import urllib.request
from .configuration_service import validate_base_url
from .network_trust import tls_context

class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        # Resolve once, validate every answer, and connect to that numeric answer.
        # This closes the DNS check/use race and prevents DNS rebinding to LAN.
        answers = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        if not answers or any(not ipaddress.ip_address(row[4][0]).is_global for row in answers):
            raise ValueError('服务商地址指向非公网 IP，已阻止请求')
        last = None
        for family, kind, protocol, _, address in answers:
            candidate = socket.socket(family, kind, protocol)
            candidate.settimeout(self.timeout)
            try:
                candidate.connect(address)
                self.sock = self._context.wrap_socket(candidate, server_hostname=self.host)
                return
            except OSError as exc:
                candidate.close(); last = exc
        raise last or OSError('无法连接服务商')

class _HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PinnedHTTPSConnection, req, context=self._context)

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('服务商返回重定向；为防止密钥泄露已拒绝，请直接配置最终 HTTPS 地址')

def urlopen(request, timeout=120, context=None):
    from urllib.parse import urlsplit
    parsed = urlsplit(request.full_url)
    validate_base_url('https://' + (parsed.netloc or ''))
    if parsed.scheme != 'https': raise ValueError('携带凭据的请求仅允许 HTTPS')
    # Do not inherit an environment proxy that could bypass public-IP pinning.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        _HTTPSHandler(context=context or tls_context()), _NoRedirect())
    return opener.open(request, timeout=timeout)
