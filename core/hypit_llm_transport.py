"""Verified GPT-6 Luna Chat tools protocol; exact endpoint, bounded JSON, no retry.

Use create_bounded_adapter with a server-owned TokenGrant. API keys are never
serialized into project files, agent messages, receipts or public exceptions.
"""
import json
import urllib.error
import urllib.request
from .hypit_token_budget import BoundedChat

class ChatRequestRejected(RuntimeError):
    def __init__(self,status,request_id,error_type,code):
        super().__init__(f'OpenLux Chat request rejected: HTTP {status}; request={request_id or "unavailable"}; code={code or "unavailable"}')
        self.status,self.request_id,self.error_type,self.code=status,request_id,error_type,code

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise PermissionError('OpenLux credential redirect refused')

class OpenLuxChatSender:
    def __init__(self,api_key,*,opener=None):
        if not isinstance(api_key,str) or not api_key.strip():raise ValueError('Backend encrypted-store API key required')
        self.__key=api_key
        self.__opener=opener or urllib.request.build_opener(_NoRedirect())

    def __call__(self,payload):
        if (payload.get('model')!='gpt-6-luna' or payload.get('reasoning_effort')!='none'
            or type(payload.get('max_completion_tokens')) is not int or not 1<=payload['max_completion_tokens']<=2048
            or payload.get('stream') is not False):
            raise PermissionError('Chat protocol or output cap not verified for this model')
        request=urllib.request.Request('https://api.openlux.ai/v1/chat/completions',method='POST',
            headers={'Authorization':'Bearer '+self.__key,'Content-Type':'application/json','Accept':'application/json',
                'User-Agent':'SeedanceStudio-Hypit-Agent/1'},
            data=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode('utf8'))
        try:
            with self.__opener.open(request,timeout=90) as response:
                if response.status!=200:raise RuntimeError('Unexpected OpenLux Chat HTTP status')
                raw=response.read(1_000_001)
                if len(raw)>1_000_000:raise ValueError('Chat response exceeds bounded limit')
                document=json.loads(raw)
                if not isinstance(document,dict):raise ValueError('Chat response must be a structured object')
                return document
        except urllib.error.HTTPError as exc:
            request_id=exc.headers.get('X-Request-Id') if exc.headers else None
            code=error_type=None
            try:
                error=json.loads(exc.read(64000)).get('error',{})
                code=str(error.get('code',''))[:120].replace(self.__key,'[REDACTED]')
                error_type=str(error.get('type',''))[:120].replace(self.__key,'[REDACTED]')
            except (ValueError,AttributeError,TypeError):pass
            if request_id:request_id=str(request_id)[:120].replace(self.__key,'[REDACTED]')
            raise ChatRequestRejected(exc.code,request_id,error_type,code) from None

def create_bounded_adapter(path,grant,api_key,*,opener=None):
    bank=BoundedChat(path,grant,OpenLuxChatSender(api_key,opener=opener))
    def adapter(payload):
        # Apply protocol parameters BEFORE budget calculation and request hashing.
        wire=dict(payload);wire['reasoning_effort']='none'
        return bank(wire)
    return adapter,bank
