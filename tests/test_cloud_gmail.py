import copy
import json
from unittest.mock import Mock

import pytest
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import AuthorizedSession

import cloud_gmail
from mail_provider import MailProviderError

START, END = '2026-10-22T00:00:00Z', '2026-10-29T00:00:00Z'


def info(user='a'):
    return {'token': user + '-old', 'refresh_token': user + '-refresh',
            'client_id': 'test', 'client_secret': 'test',
            'token_uri': cloud_gmail.TOKEN_ENDPOINT,
            'scopes': ['openid', 'email', cloud_gmail.READONLY_SCOPE]}


@pytest.fixture
def stubs(monkeypatch):
    credential = Mock()
    credential.granted_scopes = None
    refreshed = info()
    refreshed['token'] = 'a-refreshed'
    credential.to_json.return_value = json.dumps(refreshed)
    factory = Mock(return_value=credential)
    monkeypatch.setattr(Credentials, 'from_authorized_user_info', factory)
    session = Mock()
    session_factory = Mock(return_value=session)
    monkeypatch.setattr('google.auth.transport.requests.AuthorizedSession', session_factory)
    provider = Mock()
    provider.list_emails.return_value = {'messages': [], 'count': 0}
    provider_factory = Mock(return_value=provider)
    monkeypatch.setattr(cloud_gmail, 'GmailMailProvider', provider_factory)
    return credential, factory, session, session_factory, provider, provider_factory


def test_returns_refreshed_credentials_without_mutating_input(stubs, capsys):
    credential, factory, session, sf, provider, pf = stubs
    original = info()
    before = copy.deepcopy(original)
    result, updated = cloud_gmail.list_authorized_emails(original, START, END, 5)
    assert original == before
    assert updated['token'] == 'a-refreshed'
    assert result == {'messages': [], 'count': 0}
    provider.list_emails.assert_called_once_with(START, END, 5)
    assert pf.call_args.kwargs == {'authorized': True}
    session.close.assert_called_once()
    assert not capsys.readouterr().out


def test_each_call_uses_its_own_credentials_and_session(stubs):
    credential, factory, session, sf, provider, pf = stubs
    second_session = Mock()
    sf.side_effect = [session, second_session]
    cloud_gmail.list_authorized_emails(info('a'), START, END)
    cloud_gmail.list_authorized_emails(info('b'), START, END)
    assert factory.call_args_list[0].args[0]['refresh_token'] == 'a-refresh'
    assert factory.call_args_list[1].args[0]['refresh_token'] == 'b-refresh'
    assert pf.call_args_list[0].args[0].session is session
    assert pf.call_args_list[1].args[0].session is second_session
    session.close.assert_called_once()
    second_session.close.assert_called_once()


@pytest.mark.parametrize('scopes', [[], ['openid', 'email'],
    [cloud_gmail.READONLY_SCOPE, 'https://www.googleapis.com/auth/gmail.modify'],
    [cloud_gmail.READONLY_SCOPE, 'https://www.googleapis.com/auth/gmail.send'],
    [cloud_gmail.READONLY_SCOPE, 'https://mail.google.com/']])
def test_disallowed_scopes_fail_before_session(stubs, scopes):
    data = info()
    data['scopes'] = scopes
    with pytest.raises(MailProviderError):
        cloud_gmail.list_authorized_emails(data, START, END)
    stubs[1].assert_not_called()
    stubs[3].assert_not_called()


def test_wrong_endpoint_fails_before_session(stubs):
    data = info()
    data['token_uri'] = 'https://evil.example/token'
    with pytest.raises(MailProviderError):
        cloud_gmail.list_authorized_emails(data, START, END)
    stubs[3].assert_not_called()


def test_bad_range_fails_before_session(stubs):
    with pytest.raises(ValueError):
        cloud_gmail.list_authorized_emails(info(), END, START)
    stubs[3].assert_not_called()


def test_upstream_failure_is_redacted_and_session_closed(stubs):
    stubs[4].list_emails.side_effect = RuntimeError('secret-token email-body')
    with pytest.raises(MailProviderError) as exc:
        cloud_gmail.list_authorized_emails(info(), START, END)
    assert 'secret-token' not in str(exc.value)
    assert 'email-body' not in str(exc.value)
    stubs[2].close.assert_called_once()


def test_expanded_granted_scope_is_rejected(stubs):
    stubs[0].granted_scopes = [cloud_gmail.READONLY_SCOPE, 'https://www.googleapis.com/auth/gmail.modify']
    with pytest.raises(MailProviderError):
        cloud_gmail.list_authorized_emails(info(), START, END)
    stubs[2].close.assert_called_once()


def test_google_canonical_email_scope_accepted(stubs):
    data = info()
    data['scopes'] = ['openid', 'https://www.googleapis.com/auth/userinfo.email', cloud_gmail.READONLY_SCOPE]
    cloud_gmail.list_authorized_emails(data, START, END)
    stubs[3].assert_called_once()
