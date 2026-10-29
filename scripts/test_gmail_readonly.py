"""Local Gmail smoke test after explicit consent; never used by the public MCP server.

Default output contains only query range/count/coverage, never message content or IDs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import stat
import sys

# Also support `python scripts/test_gmail_readonly.py` from any working directory.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from mail_provider import GmailMailProvider, validate_request

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
TOKEN_ENDPOINT = 'https://oauth2.googleapis.com/token'
from gmail_client import GmailReadOnlyClient, GMAIL_BASE



def load_session(token_path):
    """Load only locally saved read-only credentials; never discover other accounts."""
    path = Path(token_path)
    file_stat = path.lstat()
    if not stat.S_ISREG(file_stat.st_mode) or stat.S_IMODE(file_stat.st_mode) & 0o077:
        raise ValueError('Use a regular owner-only token file.')
    info = json.loads(path.read_text(encoding='utf-8'))
    if set(info.get('scopes', [])) != set(SCOPES):
        raise ValueError('The saved token must have exactly gmail.readonly scope.')
    if info.get('token_uri') != TOKEN_ENDPOINT:
        raise ValueError('The saved token must use the Google token endpoint.')
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import AuthorizedSession
    credentials = Credentials.from_authorized_user_info(info, scopes=SCOPES)
    # Refresh is performed by AuthorizedSession if needed. Never persist or print tokens here.
    return AuthorizedSession(credentials)


def run_smoke(token_path, *, start=None, end=None, limit=5, show_content=False,
              session_factory=load_session, now=None):
    if type(limit) is not int or not 1 <= limit <= 5:
        raise ValueError('Local smoke tests read at most five messages.')
    if (start is None) != (end is None):
        raise ValueError('Provide both start and end, or neither.')
    if start is None:
        now = now or datetime.now(timezone.utc)
        start, end = (now - timedelta(days=7)).isoformat(), now.isoformat()
    validate_request(start, end, limit)
    session = session_factory(token_path)
    try:
        # A hard scan cap bounds actual message-body reads, not merely output count.
        result = GmailMailProvider(GmailReadOnlyClient(session), authorized=True,
                                   scan_limit=limit, page_limit=2).list_emails(start, end, limit)
    finally:
        session.close()
    output = {key: result[key] for key in (
        'mode', 'count', 'has_more', 'truncated', 'coverage_complete',
        'coverage_scope', 'body_content_complete')}
    output.update({'query_start': start, 'query_end': end,
                   'content_displayed': show_content, 'max_message_reads': limit})
    if show_content:
        output['messages'] = result['messages']
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read at most 5 Gmail messages after explicit user authorization. Default output is metadata only.')
    parser.add_argument('--token-file', type=Path, default=ROOT / 'token.gmail.json')
    parser.add_argument('--start', help='RFC3339 start inclusive; requires --end')
    parser.add_argument('--end', help='RFC3339 end exclusive; requires --start')
    parser.add_argument('--limit', type=int, default=5)
    parser.add_argument('--show-content', action='store_true', help='Explicitly print private message content; use only when the user requested it.')
    args = parser.parse_args(argv)
    try:
        output = run_smoke(args.token_file, start=args.start, end=args.end,
                           limit=args.limit, show_content=args.show_content)
    except Exception:
        print('Gmail smoke test did not complete. Check read-only consent, token permissions, API configuration, and network access. No message content was printed.', file=sys.stderr)
        return 1
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
