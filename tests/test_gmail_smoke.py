import base64
from datetime import datetime, timezone
import json
from unittest.mock import Mock

import pytest
from scripts import test_gmail_readonly as smoke


def session_with_mail():
    session = Mock()
    listing, message = Mock(), Mock()
    listing.json.return_value = {'messages': [{'id': 'private-message-id'}]}
    message.json.return_value = {
        'id': 'private-message-id', 'internalDate': '1793203200000',
        'payload': {'mimeType': 'text/plain', 'headers': [
            {'name': 'Subject', 'value': 'private-subject'},
            {'name': 'From', 'value': 'private-sender@example.test'}],
            'body': {'data': base64.urlsafe_b64encode(b'private-body').decode()}},
    }
    session.get.side_effect = [listing, message]
    return session


def test_default_seven_days_metadata_only():
    session = session_with_mail()
    result = smoke.run_smoke('unused', session_factory=lambda _: session,
                             now=datetime(2026, 10, 29, tzinfo=timezone.utc))
    assert result['count'] == 1
    assert result['query_start'] == '2026-10-22T00:00:00+00:00'
    assert 'private-' not in json.dumps(result)
    assert 'messages' not in result
    assert all(call.kwargs['timeout'] == 30 for call in session.get.call_args_list)
    session.close.assert_called_once()


def test_explicit_show_content():
    result = smoke.run_smoke('unused', session_factory=lambda _: session_with_mail(),
                             now=datetime(2026, 10, 29, tzinfo=timezone.utc), show_content=True)
    assert result['messages'][0]['text'] == 'private-body'


@pytest.mark.parametrize('kwargs', [{'limit': 6}, {'limit': 0}, {'limit': True}, {'start': '2026-10-22T00:00:00Z'}])
def test_invalid_request_never_loads_token(kwargs):
    factory = Mock()
    with pytest.raises(ValueError):
        smoke.run_smoke('unused', session_factory=factory, **kwargs)
    factory.assert_not_called()


def test_cli_failure_redacts_secrets(monkeypatch, capsys):
    monkeypatch.setattr(smoke, 'run_smoke', Mock(side_effect=RuntimeError('token=private-secret')))
    assert smoke.main([]) == 1
    output = capsys.readouterr()
    assert not output.out
    assert 'private-secret' not in output.err


def test_session_closes_on_failure():
    session = Mock()
    session.get.side_effect = RuntimeError('secret')
    with pytest.raises(Exception):
        smoke.run_smoke('unused', session_factory=lambda _: session)
    session.close.assert_called_once()


def test_client_only_bound_account_and_get():
    client = smoke.GmailReadOnlyClient(Mock())
    with pytest.raises(ValueError):
        client.list(userId='other-account')
    request = client.get(userId='me', id='../bad', format='full')
    assert request.url.endswith('/..%2Fbad')


@pytest.mark.parametrize('data', [
    {'scopes': ['https://www.googleapis.com/auth/gmail.modify'], 'token_uri': smoke.TOKEN_ENDPOINT},
    {'scopes': smoke.SCOPES, 'token_uri': 'https://evil.example/token'},
])
def test_token_rejects_wrong_scope_or_endpoint(tmp_path, data):
    token = tmp_path / 'token.gmail.json'
    token.write_text(json.dumps(data))
    token.chmod(0o600)
    with pytest.raises(ValueError):
        smoke.load_session(token)


def test_token_rejects_symlinks_and_shared_permissions(tmp_path):
    token = tmp_path / 'token.gmail.json'
    token.write_text('{}')
    token.chmod(0o644)
    with pytest.raises(ValueError):
        smoke.load_session(token)
    link = tmp_path / 'link.json'
    link.symlink_to(token)
    with pytest.raises(ValueError):
        smoke.load_session(link)


def test_limit_caps_actual_body_reads():
    session = Mock()
    listing, message = Mock(), Mock()
    listing.json.return_value = {'messages': [{'id': str(i)} for i in range(6)], 'nextPageToken': 'more'}
    message.json.return_value = {
        'id': 'private-id', 'internalDate': '1793203200000',
        'payload': {'mimeType': 'text/plain', 'body': {'data': ''}},
    }
    session.get.side_effect = [listing] + [message] * 5
    result = smoke.run_smoke('unused', session_factory=lambda _: session,
                             now=datetime(2026, 10, 29, tzinfo=timezone.utc))
    assert session.get.call_count == 6  # One list and no more than five get calls.
    assert result['truncated'] is True
    assert result['count'] == 5
