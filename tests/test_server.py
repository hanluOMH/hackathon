import pytest
from starlette.testclient import TestClient
from server import create_server

HEADERS = {'Accept': 'application/json, text/event-stream'}

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('HANLUWORK_MODE', 'mock')
    with TestClient(create_server().streamable_http_app()) as client:
        yield client


def rpc(client, method, params=None):
    response = client.post('/mcp', headers=HEADERS, json={
        'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {},
    })
    assert response.status_code == 200
    return response.json()


def test_initialize_and_tool_contract(client):
    initialized = rpc(client, 'initialize', {'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}})
    assert initialized['result']['serverInfo']['name'] == 'Hanluwork'
    result = rpc(client, 'tools/list')['result']['tools']
    assert [tool['name'] for tool in result] == ['list_emails']
    tool = result[0]
    assert tool['annotations']['readOnlyHint'] is True
    assert tool['annotations']['destructiveHint'] is False
    assert set(tool['inputSchema']['required']) == {'start', 'end'}


def test_call_mock_and_limit(client):
    result = rpc(client, 'tools/call', {'name':'list_emails','arguments':{'start':'2026-10-01T00:00:00Z','end':'2026-11-01T00:00:00Z','limit':1}})['result']
    assert result.get('isError') is False
    data = result['structuredContent']
    assert data['is_mock'] is True
    assert data['count'] == 1
    assert data['truncated'] is True


def test_empty_result(client):
    result = rpc(client, 'tools/call', {'name':'list_emails','arguments':{'start':'2000-01-01T00:00:00Z','end':'2000-01-02T00:00:00Z'}})['result']
    assert result['structuredContent']['messages'] == []


@pytest.mark.parametrize('arguments', [
    {'start':'bad','end':'2026-10-29T00:00:00Z'},
    {'start':'2026-10-29T00:00:00Z','end':'2026-10-01T00:00:00Z'},
    {'start':'2026-10-01T00:00:00Z','end':'2026-10-29T00:00:00Z','limit':101},
    {'start':'2026-10-01T00:00:00Z','end':'2026-10-29T00:00:00Z','limit':True},
])
def test_invalid_input_is_tool_error(client, arguments):
    result = rpc(client, 'tools/call', {'name':'list_emails','arguments':arguments})['result']
    assert result['isError'] is True


def test_real_mode_fails_closed(monkeypatch):
    monkeypatch.setenv('HANLUWORK_MODE', 'gmail')
    with pytest.raises(RuntimeError, match='disabled'):
        create_server()


def test_health(client):
    assert client.get('/health').json() == {'status':'ok','app':'Hanluwork','mode':'mock'}


def test_rebinding_host_rejected(client):
    response = client.post('/mcp', headers={**HEADERS, 'Host':'evil.example'}, json={'jsonrpc':'2.0','id':1,'method':'tools/list'})
    assert response.status_code == 421


def test_provider_error_does_not_disclose_private_data(monkeypatch):
    class FailingProvider:
        def list_emails(self, *args):
            raise RuntimeError('private-email-body secret-token')
    monkeypatch.setenv('HANLUWORK_MODE', 'mock')
    with TestClient(create_server(FailingProvider()).streamable_http_app()) as client:
        response = rpc(client, 'tools/call', {'name':'list_emails','arguments':{'start':'2026-10-01T00:00:00Z','end':'2026-10-29T00:00:00Z'}})
        assert response['result']['isError'] is True
        assert 'secret-token' not in str(response)
        assert 'private-email-body' not in str(response)
