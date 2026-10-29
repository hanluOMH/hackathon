"""Per-request Gmail reads for a separately authenticated and authorized user.

The caller must select credential_info from server-side storage using a verified
MCP identity, never an email/tool argument. Persist the returned credentials in
that same user's protected record; never return them to Gemini or logs.
"""
from __future__ import annotations

import copy
import json

from gmail_client import GmailReadOnlyClient
from mail_provider import GmailMailProvider, MailProviderError, validate_request

READONLY_SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'
ALLOWED_SCOPES = frozenset({READONLY_SCOPE, 'openid', 'email',
                           'https://www.googleapis.com/auth/userinfo.email'})
TOKEN_ENDPOINT = 'https://oauth2.googleapis.com/token'


def _validate_scopes(scopes):
    if isinstance(scopes, str):
        scopes = scopes.split()
    scope_set = set(scopes or [])
    if READONLY_SCOPE not in scope_set or not scope_set <= ALLOWED_SCOPES:
        raise ValueError('Only read-only Gmail and permitted identity scopes are accepted.')
    return list(scope_set)


def list_authorized_emails(credential_info, start, end, limit=50):
    """Return (mail_result, updated_credential_info); never share a global session.

    Authorization, user binding, encrypted persistence and refresh concurrency are
    the caller's responsibilities. This function never loads local credentials.
    """
    validate_request(start, end, limit)
    session = None
    try:
        info = copy.deepcopy(credential_info)
        if info.get('token_uri') != TOKEN_ENDPOINT:
            raise ValueError('Expected official Google token endpoint.')
        scopes = _validate_scopes(info.get('scopes'))
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import AuthorizedSession
        credentials = Credentials.from_authorized_user_info(info, scopes=scopes)
        session = AuthorizedSession(credentials)
        result = GmailMailProvider(GmailReadOnlyClient(session), authorized=True).list_emails(start, end, limit)
        # The session may have refreshed the access token during requests.
        granted_scopes = credentials.granted_scopes
        if granted_scopes is not None:
            _validate_scopes(granted_scopes)
        updated = json.loads(credentials.to_json())
        _validate_scopes(updated.get('scopes'))
        if updated.get('token_uri') != TOKEN_ENDPOINT:
            raise ValueError('Unexpected credential token endpoint.')
        return result, updated
    except Exception:
        raise MailProviderError('Unable to read authorized Gmail. Check consent and service availability.') from None
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                # A transport cleanup error must not expose response/token data.
                pass
