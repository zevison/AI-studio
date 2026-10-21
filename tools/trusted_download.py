"""HTTPS downloads with Windows/system trust plus the installed public CA bundle."""
import ssl
import urllib.request
from urllib.parse import urlsplit


def open_public_url(request, *, timeout=35, validate_url=None):
    initial=request.full_url if isinstance(request,urllib.request.Request) else str(request)
    def approved(url):
        if urlsplit(url).scheme!='https':raise ValueError('Public component downloads require HTTPS')
        if validate_url:validate_url(url)
    approved(initial)
    context=ssl.create_default_context()
    # Add public roots without replacing Windows/enterprise trust or disabling TLS.
    try:
        import certifi
        context.load_verify_locations(cafile=certifi.where())
    except ImportError:
        pass
    class SecureRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,req,fp,code,msg,headers,newurl):
            approved(newurl)
            return super().redirect_request(req,fp,code,msg,headers,newurl)
    opener=urllib.request.build_opener(urllib.request.HTTPSHandler(context=context),SecureRedirect())
    return opener.open(request, timeout=timeout)
