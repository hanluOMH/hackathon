"""Read-only mail adapters. No credentials are loaded and no client is created here.

GmailMailProvider requires a client already bound to the authenticated user.
It is not exposed by the mock server until account authorization is implemented.
"""
from __future__ import annotations

import base64
import binascii
import json
import math
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

MAX_BODY_CHARS = 12000


class MailProviderError(RuntimeError):
    """Safe, non-sensitive provider failure."""


def parse_datetime(value: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})', value
    ):
        raise ValueError('Dates must be RFC3339 timestamps with an explicit timezone.')
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)
    except ValueError:
        raise ValueError('Invalid RFC3339 date.') from None


def validate_request(start: str, end: str, limit: int):
    start_dt, end_dt = parse_datetime(start), parse_datetime(end)
    if start_dt >= end_dt:
        raise ValueError('start must be earlier than end (start inclusive, end exclusive).')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('limit must be an integer between 1 and 100.')
    return start_dt, end_dt


def result(messages, *, mode, has_more, incomplete=False):
    return {
        'mode': mode, 'is_mock': mode == 'mock', 'messages': messages,
        'count': len(messages), 'has_more': has_more,
        'truncated': has_more or incomplete,
        'coverage_complete': not has_more and not incomplete,
        'coverage_scope': 'message_list_only',
        'body_content_complete': all(not m.get('body_incomplete') and not m.get('body_truncated') for m in messages),
        'note': ('Synthetic demonstration data, not real email. ' if mode == 'mock' else '')
                + ('Partial result; do not describe this as all email in the period.'
                   if has_more or incomplete else 'All matching messages in the queried source were returned.')
                + ' Body completeness is reported separately; attachments are not read.',
    }


class MockMailProvider:
    def __init__(self, messages=None):
        self.messages = messages if messages is not None else json.loads(
            (Path(__file__).parent / 'fixtures' / 'emails.json').read_text(encoding='utf-8'))

    def list_emails(self, start: str, end: str, limit: int = 50) -> dict:
        start_dt, end_dt = validate_request(start, end, limit)
        matches = [dict(m) for m in self.messages if start_dt <= parse_datetime(m['sent_at']) < end_dt]
        matches.sort(key=lambda m: parse_datetime(m['sent_at']), reverse=True)
        return result(matches[:limit], mode='mock', has_more=len(matches) > limit)


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        elif tag in ('br', 'p', 'div', 'li') and not self.hidden:
            self.chunks.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.chunks.append(data)


def _body(part):
    """Prefer plain text within alternatives; never read attachments."""
    if part.get('filename'):
        return '', False
    parts = part.get('parts', [])
    if parts:
        if part.get('mimeType') == 'multipart/alternative':
            preferred = next((p for p in parts if p.get('mimeType') == 'text/plain'), parts[-1])
            return _body(preferred)
        decoded = [_body(p) for p in parts]
        return '\n'.join(t for t, _ in decoded if t), any(missing for _, missing in decoded)
    if part.get('mimeType') not in ('text/plain', 'text/html'):
        return '', False
    encoded = part.get('body', {}).get('data')
    if not encoded:
        return '', bool(part.get('body', {}).get('attachmentId'))
    headers = {h['name'].lower(): h['value'] for h in part.get('headers', [])}
    charset_match = re.search(r'charset=["\']?([^;\s"\']+)', headers.get('content-type', ''), re.I)
    charset = charset_match.group(1) if charset_match else 'utf-8'
    try:
        raw = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
        text = raw.decode(charset, errors='strict')
    except (ValueError, binascii.Error, UnicodeError, LookupError):
        return '', True
    if part.get('mimeType') == 'text/html':
        parser = _HTMLText()
        parser.feed(text)
        text = ''.join(parser.chunks)
    return text.strip(), False


def normalize_message(raw: dict) -> dict:
    payload = raw.get('payload', {})
    headers = {h['name'].lower(): h['value'] for h in payload.get('headers', [])}
    text, missing = _body(payload)
    when = datetime.fromtimestamp(int(raw['internalDate']) / 1000, timezone.utc)
    return {
        'id': raw['id'], 'subject': headers.get('subject', '(no subject)'),
        'sender': headers.get('from', '(unknown sender)'),
        # Gmail internal receipt date is the query basis, not the untrusted Date header.
        'sent_at': when.isoformat(), 'timestamp_basis': 'gmail_internal_date',
        'text': text[:MAX_BODY_CHARS],
        'body_truncated': len(text) > MAX_BODY_CHARS,
        'body_incomplete': missing,
        # No account-specific web URL is guessed before account identity is available.
        'source_url': None,
    }


class GmailMailProvider:
    """Adapter for a google-api-python-client compatible, authorized Gmail client.

    Caller must enforce consent and bind the client to the authenticated identity.
    Only users.messages.list/get are called with userId='me'. No write methods.
    Reads at most scan_limit messages to bound API requests. No token persistence.
    """
    def __init__(self, client, *, authorized: bool = False, scan_limit: int = 500, page_limit: int = 10):
        self.client, self.authorized = client, authorized
        self.scan_limit = max(1, min(scan_limit, 1000))
        self.page_limit = max(1, min(page_limit, 100))

    def list_emails(self, start: str, end: str, limit: int = 50) -> dict:
        start_dt, end_dt = validate_request(start, end, limit)
        if not self.authorized:
            raise MailProviderError('Gmail access requires explicit user authorization.')
        # Widen server-side search by one second; enforce exact milliseconds below.
        query = f'after:{math.floor(start_dt.timestamp()) - 1} before:{math.ceil(end_dt.timestamp()) + 1}'
        messages, seen_ids, seen_pages = [], set(), set()
        token, scanned, pages_read = None, 0, 0
        try:
            api = self.client.users().messages()
            while True:
                kwargs = dict(userId='me', q=query, maxResults=min(100, self.scan_limit - scanned), includeSpamTrash=False)
                if token:
                    kwargs['pageToken'] = token
                page = api.list(**kwargs).execute()
                pages_read += 1
                for item in page.get('messages', []):
                    if item['id'] in seen_ids:
                        continue
                    if scanned >= self.scan_limit:
                        return result(messages, mode='gmail', has_more=True, incomplete=True)
                    seen_ids.add(item['id'])
                    raw = api.get(userId='me', id=item['id'], format='full').execute()
                    scanned += 1
                    when = datetime.fromtimestamp(int(raw['internalDate']) / 1000, timezone.utc)
                    if start_dt <= when < end_dt:
                        if len(messages) >= limit:
                            return result(messages, mode='gmail', has_more=True)
                        messages.append(normalize_message(raw))
                token = page.get('nextPageToken')
                if not token:
                    return result(messages, mode='gmail', has_more=False)
                if token in seen_pages or scanned >= self.scan_limit or pages_read >= self.page_limit:
                    return result(messages, mode='gmail', has_more=True, incomplete=True)
                seen_pages.add(token)
        except Exception:
            # Upstream exception strings can contain access tokens or private messages.
            raise MailProviderError('Unable to read Gmail. Check authorization and service availability.') from None
