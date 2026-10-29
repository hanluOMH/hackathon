"""Explicit local Gmail consent helper; never reads messages or starts MCP.

Use a Desktop app OAuth client downloaded from Google Cloud. Tokens stay local.
This is not the production Gemini-to-server OAuth implementation.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
DEFAULT_TOKEN = Path(__file__).resolve().parents[1] / 'token.gmail.json'


def authorize(client_secrets: Path, token_path: Path, *, flow_factory=InstalledAppFlow):
    """Open Google consent and save only explicitly granted read-only credentials."""
    client_secrets, token_path = Path(client_secrets), Path(token_path)
    if not token_path.name.startswith('token') or token_path.suffix != '.json':
        raise ValueError('Token output must be named token*.json so the repository ignores it.')
    if token_path.exists() or token_path.is_symlink():
        raise ValueError('Token output already exists; choose another token*.json path.')
    if not token_path.parent.is_dir():
        raise ValueError('Token output directory must already exist.')
    config = json.loads(client_secrets.read_text(encoding='utf-8'))
    installed = config.get('installed', {})
    if not installed:
        raise ValueError('Use an OAuth Desktop app client secrets file.')
    if installed.get('auth_uri') != 'https://accounts.google.com/o/oauth2/auth' or installed.get('token_uri') != 'https://oauth2.googleapis.com/token':
        raise ValueError('The client must use official Google OAuth endpoints.')
    flow = flow_factory.from_client_config(config, scopes=SCOPES, autogenerate_code_verifier=True)
    credentials = flow.run_local_server(
        host='127.0.0.1', port=0, open_browser=True, timeout_seconds=300,
        authorization_prompt_message='Please review and approve read-only Gmail access in the browser.',
        success_message='Authorization received. You may close this page. No email has been read.',
        access_type='offline', prompt='consent', include_granted_scopes='false',
    )
    scopes = credentials.granted_scopes if credentials.granted_scopes is not None else credentials.scopes
    if set(scopes or []) != set(SCOPES):
        raise ValueError('The granted scope must be exactly gmail.readonly; token was not saved.')
    if not credentials.token:
        raise ValueError('Authorization returned no access token.')
    # O_EXCL rejects existing paths and symlinks without opening their targets.
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(credentials.to_json())
    except BaseException:
        token_path.unlink(missing_ok=True)
        raise
    return token_path


def main(argv=None):
    parser = argparse.ArgumentParser(description='Request local read-only Gmail authorization; never reads email.')
    parser.add_argument('--client-secrets', required=True, type=Path)
    parser.add_argument('--token-output', type=Path, default=DEFAULT_TOKEN)
    args = parser.parse_args(argv)
    try:
        path = authorize(args.client_secrets, args.token_output)
    except Exception:
        # OAuth errors may embed codes, tokens, or private configuration.
        print('Authorization was not completed. Check the Desktop OAuth configuration, consent, and output path. No email was read.', file=sys.stderr)
        return 1
    print(f'Authorization saved locally with owner-only permissions: {path}. No email was read.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
