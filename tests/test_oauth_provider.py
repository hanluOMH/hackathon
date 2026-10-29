import asyncio
import time
from urllib.parse import urlsplit, parse_qs
from unittest.mock import AsyncMock
import pytest
from cryptography.fernet import Fernet
from starlette.applications import Starlette
from starlette.testclient import TestClient
from mcp.shared.auth import OAuthClientInformationFull
from mcp.server.auth.provider import AuthorizationParams, TokenError, RegistrationError
from oauth_provider import HanluOAuthProvider, SCOPE, COOKIE, key
from oauth_store import MemoryStore

BASE='https://app.example'
REDIRECT='https://oauth-redirect.googleusercontent.com/r/hanluwork'

def setup():
    store=MemoryStore()
    provider=HanluOAuthProvider(store,BASE,'google-client','secret',{'test@example.com'},Fernet.generate_key())
    client=OAuthClientInformationFull(client_id='gemini',client_secret='test-client-secret',token_endpoint_auth_method='client_secret_post',redirect_uris=[REDIRECT],scope=SCOPE,grant_types=['authorization_code','refresh_token'])
    return provider,client,store


def test_callback_consent_tokens_and_revocation():
    provider,client,store=setup()
    provider.exchange_google_code=AsyncMock(return_value=({'sub':'user1','email':'test@example.com'},{'token':'private-google-token'}))
    async def begin():
        await provider.register_client(client)
        return await provider.authorize(client,AuthorizationParams(state='gemini-state',scopes=[SCOPE],code_challenge='challenge',redirect_uri=REDIRECT,redirect_uri_provided_explicitly=True,resource=BASE+'/mcp'))
    url=asyncio.run(begin())
    with TestClient(Starlette(routes=provider.routes()),base_url=BASE) as browser:
        login=browser.get(url,follow_redirects=False)
        state=parse_qs(urlsplit(login.headers['location']).query)['state'][0]
        response=browser.get('/oauth/google/callback',params={'state':state,'code':'google-code'})
        assert response.status_code==200
        assert 'private-google-token' not in response.text
        import re
        flow=re.search('name="flow" value="([^"]+)"',response.text).group(1)
        denied=browser.post('/oauth/consent',data={'flow':flow,'decision':'allow'},headers={'Origin':'https://attacker.example'})
        assert denied.status_code==400
        consent=browser.post('/oauth/consent',data={'flow':flow,'decision':'allow'},headers={'Origin':BASE},follow_redirects=False)
        assert consent.status_code==302
        query=parse_qs(urlsplit(consent.headers['location']).query)
        assert query['state']==['gemini-state']
        code=query['code'][0]
        assert browser.get('/oauth/google/callback',params={'state':state,'code':'google-code'}).status_code==400
    async def exchange():
        authorization=await provider.load_authorization_code(client,code)
        attempts=await asyncio.gather(provider.exchange_authorization_code(client,authorization),provider.exchange_authorization_code(client,authorization),return_exceptions=True)
        assert sum(isinstance(v,TokenError) for v in attempts)==1
        tokens=next(v for v in attempts if not isinstance(v,Exception))
        access=await provider.load_access_token(tokens.access_token)
        assert access.subject=='user1' and access.resource==BASE+'/mcp'
        assert tokens.access_token!='private-google-token'
        refresh=await provider.load_refresh_token(client,tokens.refresh_token)
        again=await provider.exchange_refresh_token(client,refresh,[SCOPE])
        assert again.refresh_token==tokens.refresh_token
        with pytest.raises(TokenError):
            await provider.exchange_refresh_token(client,refresh,['write'])
        await provider.revoke_token(refresh)
        assert await provider.load_access_token(tokens.access_token) is None
        assert await provider.load_access_token(again.access_token) is None
        assert await provider.load_refresh_token(client,tokens.refresh_token) is None
        assert await provider.google_credentials('user1')=={'token':'private-google-token'}
        with pytest.raises(ValueError):
            await provider.google_credentials('user2')
    asyncio.run(exchange())
    assert 'private-google-token' not in str(store.data)


@pytest.mark.parametrize('uri',['http://oauth-redirect.googleusercontent.com/r/a','https://evil.example/r/a','https://oauth-redirect.googleusercontent.com.evil.example/r/a','https://u@oauth-redirect.googleusercontent.com/r/a','https://oauth-redirect.googleusercontent.com/r/a?x=1','https://oauth-redirect.googleusercontent.com/r/a#x','https://oauth-redirect.googleusercontent.com/other/a'])
def test_redirect_validation(uri):
    provider,client,_=setup()
    assert not provider.allowed_redirect(uri)
    client.redirect_uris=[uri]
    with pytest.raises(RegistrationError):
        asyncio.run(provider.register_client(client))


def test_cookie_binding_and_expiry():
    provider,client,_=setup()
    provider.exchange_google_code=AsyncMock()
    async def pending():
        await provider.store.put(key('google_state','state'),{'expires_at':time.time()+60,'cookie_hash':'not-matching'})
    asyncio.run(pending())
    with TestClient(Starlette(routes=provider.routes()),base_url=BASE) as browser:
        assert browser.get('/oauth/google/callback?state=state&code=code').status_code==400
    provider.exchange_google_code.assert_not_called()


def test_exact_allowlist():
    provider,_,_=setup()
    provider.redirect_allowlist={REDIRECT}
    assert provider.allowed_redirect(REDIRECT)
    assert not provider.allowed_redirect('https://oauth-redirect.googleusercontent.com/r/other')


def test_sdk_pkce_rejects_wrong_verifier_before_exchange():
    from mcp.server.fastmcp import FastMCP
    from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
    from oauth_provider import challenge
    provider,client,_=setup()
    async def seed():
        await provider.register_client(client)
        await provider.store.put(key('code','issued-code'),{
            'scopes':[SCOPE],'expires_at':time.time()+60,'client_id':'gemini',
            'code_challenge':challenge('correct-verifier'),'redirect_uri':REDIRECT,
            'redirect_uri_provided_explicitly':True,'resource':BASE+'/mcp','subject':'user1'})
    asyncio.run(seed())
    server=FastMCP('Test',auth_server_provider=provider,auth=AuthSettings(issuer_url=BASE,resource_server_url=BASE+'/mcp',required_scopes=[SCOPE],validate_token_resource=True,client_registration_options=ClientRegistrationOptions(enabled=True,valid_scopes=[SCOPE],default_scopes=[SCOPE])))
    with TestClient(server.streamable_http_app(),base_url=BASE) as browser:
        args={'grant_type':'authorization_code','client_id':'gemini','client_secret':'test-client-secret','code':'issued-code','redirect_uri':REDIRECT,'code_verifier':'incorrect'}
        invalid=browser.post('/token',data=args)
        assert invalid.status_code==400 and invalid.json()['error']=='invalid_grant'
        args['code_verifier']='correct-verifier'
        valid=browser.post('/token',data=args)
        assert valid.status_code==200
        assert valid.json()['token_type']=='Bearer'
        assert browser.post('/token',data=args).status_code==400


@pytest.mark.parametrize('field,value',[('nonce','wrong'),('iss','https://evil.example'),('email','other@example.com'),('email_verified',False)])
def test_google_identity_checks(monkeypatch,field,value):
    import oauth_provider as module
    provider,_,_=setup()
    token={'id_token':'signed-token','scope':'https://www.googleapis.com/auth/gmail.readonly','access_token':'secret-access','refresh_token':'secret-refresh','expires_in':3600}
    class Response:
        def raise_for_status(self): pass
        def json(self): return token
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,*args,**kwargs): return Response()
    identity={'sub':'user1','email':'test@example.com','email_verified':True,'nonce':'nonce','iss':'https://accounts.google.com'}
    identity[field]=value
    from unittest.mock import Mock
    verifier=Mock(return_value=identity)
    monkeypatch.setattr(module.httpx,'AsyncClient',lambda **kwargs:Client())
    monkeypatch.setattr(module.id_token,'verify_oauth2_token',verifier)
    with pytest.raises(ValueError):
        asyncio.run(provider.exchange_google_code('code','verifier','nonce'))
    assert verifier.call_args.args[2]=='google-client'


def test_invalid_port_returns_false():
    provider,_,_=setup()
    assert not provider.allowed_redirect('https://oauth-redirect.googleusercontent.com:bad/r/app')


def test_credentials_refresh_does_not_overwrite_new_authorization():
    provider,_,_=setup()
    async def run():
        await provider.save_google_credentials('u',{'refresh_token':'old','token':'old-access'})
        await provider.save_google_credentials('u',{'refresh_token':'new','token':'new-access'})
        assert not await provider.save_google_credentials('u',{'refresh_token':'old','token':'late-access'},expected_refresh_token='old')
        assert (await provider.google_credentials('u'))['token']=='new-access'
        assert await provider.save_google_credentials('u',{'refresh_token':'new','token':'refreshed'},expected_refresh_token='new')
    asyncio.run(run())


def test_ttl_metadata_and_revoked_grant_cannot_revive():
    from datetime import datetime
    provider,client,store=setup()
    async def run():
        grant='grant'
        await provider.store.put(key('grant',grant),{'active':True})
        record={'client_id':client.client_id,'scopes':[SCOPE],'resource':BASE+'/mcp','subject':'u','grant':grant,'expires_at':int(time.time())+300}
        await provider.store.put(key('refresh','refresh'),record)
        assert isinstance(store.data[key('refresh','refresh')]['expires_at'],datetime)
        refresh=await provider.load_refresh_token(client,'refresh')
        # Emulate refresh reading before revoke then issuing after revoke.
        snapshot=await provider.live_record('refresh','refresh')
        await provider.revoke_token(refresh)
        tokens=await provider.issue_access(snapshot,'refresh')
        assert await provider.load_access_token(tokens.access_token) is None
        assert await provider.load_refresh_token(client,'refresh') is None
        with pytest.raises(TokenError):
            await provider.exchange_refresh_token(client,refresh,[SCOPE])
    asyncio.run(run())


@pytest.mark.parametrize('extra_scope',[None,'https://www.googleapis.com/auth/gmail.modify'])
def test_google_granted_scopes_are_preserved_and_extra_denied(monkeypatch,extra_scope):
    import oauth_provider as module
    provider,_,_=setup()
    scopes='openid https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/gmail.readonly'
    if extra_scope:
        scopes+=' '+extra_scope
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'id_token':'signed','scope':scopes,'access_token':'access','refresh_token':'refresh','expires_in':3600}
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,*args,**kwargs): return Response()
    monkeypatch.setattr(module.httpx,'AsyncClient',lambda **kwargs:Client())
    monkeypatch.setattr(module.id_token,'verify_oauth2_token',lambda *args:{'sub':'u','email':'test@example.com','email_verified':True,'nonce':'nonce','iss':'https://accounts.google.com'})
    if extra_scope:
        with pytest.raises(ValueError):
            asyncio.run(provider.exchange_google_code('code','verifier','nonce'))
    else:
        _,credentials=asyncio.run(provider.exchange_google_code('code','verifier','nonce'))
        assert set(credentials['scopes'])==set(scopes.split())


def test_dcr_rejects_public_client():
    provider,client,_=setup()
    client.token_endpoint_auth_method='none'
    with pytest.raises(RegistrationError,match='Confidential'):
        asyncio.run(provider.register_client(client))


def test_legacy_public_client_cannot_be_loaded():
    provider,client,_=setup()
    client.token_endpoint_auth_method='none'
    async def run():
        await provider.store.put(key('client',client.client_id),client.model_dump(mode='json'))
        assert await provider.get_client(client.client_id) is None
    asyncio.run(run())
