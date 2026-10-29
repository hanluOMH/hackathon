"""Shared read-only Gmail REST adapter; receives an already authorized session.

No credential loading, identity selection, persistence, or authorization occurs here.
"""
from urllib.parse import quote

GMAIL_BASE = 'https://gmail.googleapis.com/gmail/v1/users/me/messages'


class _Request:
    def __init__(self, session, url, params):
        self.session, self.url, self.params = session, url, params

    def execute(self):
        response = self.session.get(self.url, params=self.params, timeout=30)
        response.raise_for_status()
        return response.json()


class GmailReadOnlyClient:
    """Minimal google-api-client-compatible adapter exposing only list/get."""
    def __init__(self, session):
        self.session = session

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, *, userId, **params):
        if userId != 'me':
            raise ValueError('Only the authorized account is supported.')
        return _Request(self.session, GMAIL_BASE, params)

    def get(self, *, userId, id, format):
        if userId != 'me' or format != 'full':
            raise ValueError('Only read-only full messages of the authorized account are supported.')
        return _Request(self.session, GMAIL_BASE + '/' + quote(id, safe=''), {'format': 'full'})

