"""Separate Hanluwork OAuth issuer; Google tokens never become MCP bearer tokens."""
import asyncio
import base64
import hashlib
import html
import secrets
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode, urlsplit

import httpx
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from mcp.server.auth.provider import AccessToken, AuthorizationCode, RefreshToken, AuthorizeError, TokenError, RegistrationError
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route
from oauth_store import CipherStore

SCOPE = 'gmail.read'
GOOGLE_SCOPES = ['openid', 'email', 'https://www.googleapis.com/auth/gmail.readonly']
COOKIE = '__Host-hanluwork_oauth'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def challenge(value):
    return base64.urlsafe_b64encode(hashlib.sha256(value.encode()).digest()).decode().rstrip('=')


def key(kind, value):
    return kind + '_' + digest(value)


class HanluOAuthProvider:
    def __init__(self, store, base_url, google_client_id, google_client_secret, allowed_emails, encryption_key, redirect_allowlist=None):
        parts = urlsplit(base_url)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.query or parts.fragment or parts.path not in ('', '/'):
            raise ValueError('PUBLIC_BASE_URL must be an HTTPS origin.')
        if not allowed_emails:
            raise ValueError('An explicit allowed email list is required.')
        self.store = CipherStore(store, encryption_key)
        self.base_url = base_url.rstrip('/')
        self.resource = self.base_url + '/mcp'
        self.google_client_id, self.google_client_secret = google_client_id, google_client_secret
        self.allowed_emails = {email.lower() for email in allowed_emails}
        self.redirect_allowlist = set(redirect_allowlist or [])

    def allowed_redirect(self, uri):
        try:
            p = urlsplit(str(uri))
            port = p.port
        except ValueError:
            return False
        hosts = {'oauth-redirect.googleusercontent.com', 'oauth-redirect-sandbox.googleusercontent.com', 'oauth-redirect-test.googleusercontent.com'}
        if p.scheme != 'https' or p.hostname not in hosts or p.username or p.password or port or p.query or p.fragment:
            return False
        if not (p.path.startswith('/r/') or p.path.startswith('/a/')) or len(p.path.split('/')) != 3 or not p.path.split('/')[-1]:
            return False
        return not self.redirect_allowlist or str(uri) in self.redirect_allowlist

    async def get_client(self, client_id):
        value = await self.store.get(key('client', client_id))
        if not value or value.get('token_endpoint_auth_method') not in {'client_secret_post', 'client_secret_basic'}:
            return None
        return OAuthClientInformationFull.model_validate(value)

    async def register_client(self, client_info):
        if client_info.token_endpoint_auth_method not in {'client_secret_post', 'client_secret_basic'}:
            raise RegistrationError('invalid_client_metadata', 'Confidential client authentication is required.')
        if not client_info.redirect_uris or any(not self.allowed_redirect(uri) for uri in client_info.redirect_uris):
            raise RegistrationError('invalid_redirect_uri', 'Only approved Gemini HTTPS callbacks are accepted.')
        if client_info.scope and set(client_info.scope.split()) != {SCOPE}:
            raise RegistrationError('invalid_client_metadata', 'Only gmail.read is supported.')
        if set(client_info.grant_types) - {'authorization_code', 'refresh_token'}:
            raise RegistrationError('invalid_client_metadata', 'Unsupported grant.')
        await self.store.put(key('client', client_info.client_id), client_info.model_dump(mode='json'))

    async def authorize(self, client, params):
        if not self.allowed_redirect(params.redirect_uri) or params.resource not in (None, self.resource):
            raise AuthorizeError('invalid_request', 'Invalid redirect or resource.')
        if params.scopes and set(params.scopes) != {SCOPE}:
            raise AuthorizeError('invalid_scope', 'Only gmail.read is supported.')
        pending_id = secrets.token_urlsafe(32)
        await self.store.put(key('pending', pending_id), {
            'client_id':client.client_id, 'params':params.model_dump(mode='json'),
            'expires_at':time.time()+600,
        })
        return self.base_url + '/oauth/login?' + urlencode({'flow':pending_id})

    def routes(self):
        return [Route('/oauth/login', self.login), Route('/oauth/google/callback', self.callback), Route('/oauth/consent', self.consent, methods=['POST'])]

    @staticmethod
    def failure():
        return HTMLResponse('Authorization could not be completed. Restart the connection from Gemini.', status_code=400, headers={'Cache-Control':'no-store'})

    async def login(self, request):
        pending = await self.store.consume(key('pending', request.query_params.get('flow','')))
        if not pending or pending['expires_at'] < time.time():
            return self.failure()
        state, cookie, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(4))
        pending.update(cookie_hash=digest(cookie), nonce=nonce, verifier=verifier)
        await self.store.put(key('google_state',state),pending)
        url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
            'client_id':self.google_client_id, 'redirect_uri':self.base_url+'/oauth/google/callback',
            'response_type':'code','scope':' '.join(GOOGLE_SCOPES),'state':state,'nonce':nonce,
            'code_challenge':challenge(verifier),'code_challenge_method':'S256',
            'access_type':'offline','prompt':'consent select_account','include_granted_scopes':'false',
        })
        response = RedirectResponse(url, status_code=302, headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
        response.set_cookie(COOKIE,cookie,secure=True,httponly=True,samesite='lax',max_age=600,path='/')
        return response

    async def exchange_google_code(self, code, verifier, nonce):
        async with httpx.AsyncClient(timeout=20) as http:
            response = await http.post('https://oauth2.googleapis.com/token',data={
                'client_id':self.google_client_id,'client_secret':self.google_client_secret,
                'code':code,'code_verifier':verifier,'grant_type':'authorization_code',
                'redirect_uri':self.base_url+'/oauth/google/callback',
            })
            response.raise_for_status()
            token = response.json()
        identity = await asyncio.to_thread(id_token.verify_oauth2_token,token['id_token'],GoogleRequest(),self.google_client_id)
        if identity.get('iss') not in ('https://accounts.google.com','accounts.google.com') or identity.get('nonce') != nonce:
            raise ValueError('Invalid Google identity.')
        if not identity.get('email_verified') or identity.get('email','').lower() not in self.allowed_emails or not identity.get('sub'):
            raise ValueError('Account not permitted.')
        granted = set(token.get('scope','').split())
        allowed = set(GOOGLE_SCOPES) | {'https://www.googleapis.com/auth/userinfo.email'}
        if 'https://www.googleapis.com/auth/gmail.readonly' not in granted or granted - allowed:
            raise ValueError('Only the requested read-only Gmail and identity scopes are accepted.')
        if not token.get('refresh_token'):
            raise ValueError('Offline access is required.')
        credentials = {'token':token['access_token'],'refresh_token':token['refresh_token'],
            'token_uri':'https://oauth2.googleapis.com/token','client_id':self.google_client_id,
            'client_secret':self.google_client_secret,'scopes':sorted(granted),
            'expiry':(datetime.now(timezone.utc)+timedelta(seconds=int(token['expires_in']))).isoformat().replace('+00:00','Z')}
        return identity,credentials

    async def callback(self, request):
        state = request.query_params.get('state','')
        pending = await self.store.get(key('google_state',state))
        cookie = request.cookies.get(COOKIE,'')
        if not pending or pending['expires_at'] < time.time() or not secrets.compare_digest(pending['cookie_hash'],digest(cookie)):
            return self.failure()
        pending = await self.store.consume(key('google_state',state))
        if not pending or request.query_params.get('error') or not request.query_params.get('code'):
            return self.failure()
        try:
            identity,credentials = await self.exchange_google_code(request.query_params['code'],pending['verifier'],pending['nonce'])
        except Exception:
            return self.failure()
        consent_id = secrets.token_urlsafe(32)
        pending.update(subject=identity['sub'],email=identity['email'],credentials=credentials)
        await self.store.put(key('consent',consent_id),pending)
        return HTMLResponse(
            '<!doctype html><html><head><meta charset="utf-8"><title>Hanluwork authorization</title></head><body>'
            '<h1>Connect Hanluwork to Gemini</h1><p>Allow Gemini to read and summarize Gmail for '
            +html.escape(identity['email'])+' through Hanluwork? This does not allow sending or modifying email.</p>'
            '<form method="post" action="/oauth/consent"><input type="hidden" name="flow" value="'+consent_id+'">'
            '<button name="decision" value="allow">Allow read-only access</button> '
            '<button name="decision" value="deny">Cancel</button></form></body></html>',
            headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'none'; form-action 'self'; frame-ancestors 'none'"})

    async def consent(self, request):
        if request.headers.get('origin') != self.base_url:
            return self.failure()
        form = await request.form()
        consent_id = str(form.get('flow',''))
        pending = await self.store.get(key('consent',consent_id))
        if not pending or pending['expires_at'] < time.time() or not secrets.compare_digest(pending['cookie_hash'],digest(request.cookies.get(COOKIE,''))):
            return self.failure()
        pending = await self.store.consume(key('consent',consent_id))
        if not pending:
            return self.failure()
        params = pending['params']
        query = {'state':params['state']} if params.get('state') else {}
        if form.get('decision') != 'allow':
            query['error']='access_denied'
        else:
            await self.save_google_credentials(pending['subject'],pending['credentials'])
            code = secrets.token_urlsafe(32)
            value = AuthorizationCode(code=code,scopes=[SCOPE],expires_at=time.time()+90,
                client_id=pending['client_id'],code_challenge=params['code_challenge'],
                redirect_uri=params['redirect_uri'],redirect_uri_provided_explicitly=params['redirect_uri_provided_explicitly'],
                resource=self.resource,subject=pending['subject']).model_dump(mode='json')
            value.pop('code')
            await self.store.put(key('code',code),value)
            query['code']=code
        response=RedirectResponse(params['redirect_uri']+'?'+urlencode(query),status_code=302,headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
        response.delete_cookie(COOKIE,secure=True,httponly=True,samesite='lax',path='/')
        return response

    async def save_google_credentials(self, subject, credential_info, *, expected_refresh_token=None):
        if expected_refresh_token is None:
            await self.store.put(key('google_user',subject),credential_info)
            return True
        def replace_if_current(current):
            if not current or current.get('refresh_token') != expected_refresh_token:
                return current
            return credential_info
        saved = await self.store.update(key('google_user',subject), replace_if_current)
        return saved == credential_info

    async def google_credentials(self, subject):
        value=await self.store.get(key('google_user',subject))
        if not value:
            raise ValueError('Google account is not connected.')
        return value

    async def load_authorization_code(self, client, authorization_code):
        value=await self.store.get(key('code',authorization_code))
        if not value or value['client_id'] != client.client_id:
            return None
        return AuthorizationCode(code=authorization_code,**value)

    async def exchange_authorization_code(self, client, authorization_code):
        value=await self.store.consume(key('code',authorization_code.code))
        if not value or value['client_id'] != client.client_id or value['expires_at'] < time.time():
            raise TokenError('invalid_grant','Authorization code is invalid or already used.')
        grant=secrets.token_urlsafe(32)
        await self.store.put(key('grant',grant),{'active':True})
        refresh=secrets.token_urlsafe(32)
        record={**{k:value[k] for k in ['client_id','scopes','resource','subject']},'grant':grant,'expires_at':int(time.time())+30*86400}
        await self.store.put(key('refresh',refresh),record)
        return await self.issue_access(record,refresh)

    async def issue_access(self,record,refresh):
        access=secrets.token_urlsafe(32)
        await self.store.put(key('access',access),{**record,'expires_at':int(time.time())+3600})
        return OAuthToken(access_token=access,token_type='Bearer',expires_in=3600,refresh_token=refresh,scope=' '.join(record['scopes']))

    async def live_record(self, kind, token):
        record=await self.store.get(key(kind,token))
        if not record or record['expires_at'] < time.time():
            return None
        grant=await self.store.get(key('grant',record['grant']))
        return record if grant and grant.get('active') else None

    async def load_access_token(self,token):
        record=await self.live_record('access',token)
        return AccessToken(token=token,**{k:v for k,v in record.items() if k!='grant'}) if record else None

    async def load_refresh_token(self,client,refresh_token):
        record=await self.live_record('refresh',refresh_token)
        if not record or record['client_id'] != client.client_id:
            return None
        return RefreshToken(token=refresh_token,**{k:v for k,v in record.items() if k!='grant'})

    async def exchange_refresh_token(self,client,refresh_token,scopes):
        record=await self.live_record('refresh',refresh_token.token)
        if not record or record['client_id']!=client.client_id or set(scopes)-set(record['scopes']):
            raise TokenError('invalid_grant','Refresh token is invalid.')
        record['expires_at']=int(time.time())+30*86400
        await self.store.put(key('refresh',refresh_token.token),record)
        return await self.issue_access({**record,'scopes':scopes},refresh_token.token)

    async def revoke_token(self,token):
        kind='refresh' if isinstance(token,RefreshToken) else 'access'
        record=await self.store.get(key(kind,token.token))
        if record:
            await self.store.put(key('grant',record['grant']),{'active':False})
