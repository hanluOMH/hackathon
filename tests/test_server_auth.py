"""Real-mode auth integration tests; Google and credentials are synthetic stubs."""
import copy
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from mcp.server.auth.provider import AccessToken
from starlette.testclient import TestClient

from server import create_server


class FakeOAuth:
    base_url = 'https://hanluwork.example.test'

    def __init__(self):
        self.tokens = {
            name: AccessToken(token=name, client_id='gemini-test', subject=name,
                              scopes=['gmail.read'], resource=self.base_url + '/mcp',
                              expires_at=int(time.time()) + 300)
            for name in ['alice', 'bob', 'unlinked']}
        self.credentials = {'alice': {'token': 'alice-google-secret', 'refresh_token': 'alice-refresh-secret'}, 'bob': {'token': 'bob-google-secret', 'refresh_token': 'bob-refresh-secret'}}
        self.loaded, self.saved, self.expected_refresh_tokens = [], [], []
        self.load_error, self.save_error = None, None

    def routes(self):
        return []

    async def load_access_token(self, token):
        return self.tokens.get(token)

    async def google_credentials(self, subject):
        self.loaded.append(subject)
        if self.load_error:
            raise self.load_error
        return copy.deepcopy(self.credentials.get(subject))

    async def save_google_credentials(self, subject, updated, *, expected_refresh_token=None):
        if self.save_error:
            raise self.save_error
        self.expected_refresh_tokens.append((subject, expected_refresh_token))
        assert expected_refresh_token == self.credentials[subject]["refresh_token"]
        self.saved.append((subject, copy.deepcopy(updated)))


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv('HANLUWORK_MODE', 'gmail')
    oauth = FakeOAuth()
    reader = Mock(side_effect=lambda credentials, *args: (
        {'is_mock': False, 'messages': [{'id': credentials['token'].split('-')[0]}]},
        {'token': credentials['token'] + '-refreshed'}))
    monkeypatch.setattr('cloud_gmail.list_authorized_emails', reader)
    with TestClient(create_server(oauth_provider=oauth).streamable_http_app()) as client:
        yield client, oauth, reader


def request(client, token=None, method='tools/call'):
    headers = {'Accept': 'application/json, text/event-stream'}
    if token is not None:
        headers['Authorization'] = 'Bearer ' + token
    return client.post('/mcp', headers=headers, json={
        'jsonrpc': '2.0', 'id': 1, 'method': method,
        'params': {'name': 'list_emails', 'arguments': {
            'start': '2026-10-22T00:00:00Z', 'end': '2026-10-29T00:00:00Z', 'limit': 5}}
        if method == 'tools/call' else {},
    })


@pytest.mark.parametrize('token', [None, 'forged'])
def test_missing_or_invalid_token_is_401_before_provider(setup, token):
    client, oauth, reader = setup
    response = request(client, token)
    assert response.status_code == 401
    assert 'resource_metadata=' in response.headers['www-authenticate']
    assert oauth.loaded == []
    reader.assert_not_called()


def test_expired_token_is_401(setup):
    client, oauth, reader = setup
    oauth.tokens['alice'].expires_at = int(time.time()) - 1
    assert request(client, 'alice').status_code == 401
    reader.assert_not_called()


def test_wrong_audience_is_401(setup):
    client, oauth, reader = setup
    oauth.tokens['alice'].resource = 'https://another.example/mcp'
    assert request(client, 'alice').status_code == 401
    reader.assert_not_called()


def test_missing_scope_is_403(setup):
    client, oauth, reader = setup
    oauth.tokens['alice'].scopes = []
    assert request(client, 'alice').status_code == 403
    assert oauth.loaded == []
    reader.assert_not_called()


def test_valid_call_binds_and_saves_only_same_subject(setup):
    client, oauth, reader = setup
    response = request(client, 'alice')
    assert response.status_code == 200
    data = response.json()['result']
    assert data.get('isError') is False
    assert data['structuredContent']['messages'] == [{'id': 'alice'}]
    assert oauth.loaded == ['alice']
    assert oauth.expected_refresh_tokens == [('alice', 'alice-refresh-secret')]
    assert oauth.saved == [('alice', {'token': 'alice-google-secret-refreshed'})]
    assert 'google-secret' not in response.text


def test_concurrent_subjects_remain_isolated(setup):
    client, oauth, reader = setup
    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(executor.map(lambda token: request(client, token), ['alice', 'bob']))
    assert [r.json()['result']['structuredContent']['messages'][0]['id'] for r in replies] == ['alice', 'bob']
    assert sorted(oauth.expected_refresh_tokens) == [('alice', 'alice-refresh-secret'), ('bob', 'bob-refresh-secret')]
    assert sorted(oauth.saved) == [
        ('alice', {'token': 'alice-google-secret-refreshed'}),
        ('bob', {'token': 'bob-google-secret-refreshed'})]


def test_missing_link_returns_tool_error_without_google_call(setup):
    client, oauth, reader = setup
    response = request(client, 'unlinked')
    assert response.json()['result']['isError'] is True
    reader.assert_not_called()
    assert oauth.saved == []


def test_missing_subject_returns_tool_error_without_google_call(setup):
    client, oauth, reader = setup
    oauth.tokens['alice'].subject = None
    assert request(client, 'alice').json()['result']['isError'] is True
    assert oauth.loaded == []
    reader.assert_not_called()


@pytest.mark.parametrize('failure_site', ['load', 'read', 'save'])
@pytest.mark.parametrize('error_type', [RuntimeError, ValueError])
def test_private_errors_are_redacted(setup, failure_site, error_type, caplog):
    client, oauth, reader = setup
    error = error_type('private-google-secret private-email-body')
    if failure_site == 'load':
        oauth.load_error = error
    elif failure_site == 'read':
        reader.side_effect = error
    else:
        oauth.save_error = error
    response = request(client, 'alice')
    assert response.json()['result']['isError'] is True
    assert 'private-google-secret' not in response.text
    assert 'private-email-body' not in response.text
    assert 'private-google-secret' not in caplog.text
    assert 'private-email-body' not in caplog.text


def test_tools_discovery_also_requires_auth(setup):
    client, oauth, reader = setup
    assert request(client, method='tools/list').status_code == 401
    response = request(client, 'alice', method='tools/list')
    assert response.status_code == 200
    assert response.json()['result']['tools'][0]['name'] == 'list_emails'
    reader.assert_not_called()
