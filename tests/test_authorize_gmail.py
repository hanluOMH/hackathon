import json
import stat
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from scripts import authorize_gmail as helper

@pytest.fixture
def inputs(tmp_path):
    client = tmp_path / 'credentials.json'
    client.write_text(json.dumps({'installed': {'client_id':'test', 'client_secret':'test', 'auth_uri':'https://accounts.google.com/o/oauth2/auth', 'token_uri':'https://oauth2.googleapis.com/token'}}))
    credentials = SimpleNamespace(granted_scopes=helper.SCOPES, scopes=helper.SCOPES, token='sensitive-token', to_json=lambda: '{"token":"sensitive-token"}')
    factory = Mock()
    factory.from_client_config.return_value.run_local_server.return_value = credentials
    return client, tmp_path / 'token.gmail.json', factory


def test_consent_is_readonly_loopback_and_private_file(inputs, capsys):
    client, token, factory = inputs
    helper.authorize(client, token, flow_factory=factory)
    kwargs = factory.from_client_config.call_args.kwargs
    assert kwargs['scopes'] == ['https://www.googleapis.com/auth/gmail.readonly']
    assert kwargs['autogenerate_code_verifier'] is True
    kwargs = factory.from_client_config.return_value.run_local_server.call_args.kwargs
    assert kwargs['host'] == '127.0.0.1'
    assert kwargs['open_browser'] is True
    assert kwargs['include_granted_scopes'] == 'false'
    assert kwargs['prompt'] == 'consent'
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert json.loads(token.read_text())['token'] == 'sensitive-token'
    assert 'sensitive-token' not in capsys.readouterr().out


def test_existing_file_not_overwritten(inputs):
    client, token, factory = inputs
    token.write_text('existing')
    with pytest.raises(ValueError, match='exists'):
        helper.authorize(client, token, flow_factory=factory)
    factory.from_client_config.assert_not_called()
    assert token.read_text() == 'existing'


def test_symlink_not_followed(inputs):
    client, token, factory = inputs
    token.symlink_to(token.parent / 'missing')
    with pytest.raises(ValueError, match='exists'):
        helper.authorize(client, token, flow_factory=factory)
    factory.from_client_config.assert_not_called()


def test_only_ignored_output_name(inputs):
    client, token, factory = inputs
    with pytest.raises(ValueError, match='token'):
        helper.authorize(client, token.with_name('public.json'), flow_factory=factory)
    factory.from_client_config.assert_not_called()


@pytest.mark.parametrize('granted', [[], ['https://www.googleapis.com/auth/gmail.modify'], helper.SCOPES + ['profile']])
def test_scope_mismatch_not_persisted(inputs, granted):
    client, token, factory = inputs
    factory.from_client_config.return_value.run_local_server.return_value.granted_scopes = granted
    with pytest.raises(ValueError, match='scope'):
        helper.authorize(client, token, flow_factory=factory)
    assert not token.exists()


def test_denied_consent_no_file(inputs):
    client, token, factory = inputs
    factory.from_client_config.return_value.run_local_server.side_effect = RuntimeError('denied')
    with pytest.raises(RuntimeError):
        helper.authorize(client, token, flow_factory=factory)
    assert not token.exists()


def test_wrong_endpoint_rejected_before_browser(inputs):
    client, token, factory = inputs
    data = json.loads(client.read_text())
    data['installed']['token_uri'] = 'https://bad.example/token'
    client.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='official Google'):
        helper.authorize(client, token, flow_factory=factory)
    factory.from_client_config.assert_not_called()


def test_cli_failure_redacted(inputs, monkeypatch, capsys):
    client, token, factory = inputs
    monkeypatch.setattr(helper, 'authorize', Mock(side_effect=RuntimeError('secret-token')))
    assert helper.main(['--client-secrets', str(client)]) == 1
    assert 'secret-token' not in capsys.readouterr().err
