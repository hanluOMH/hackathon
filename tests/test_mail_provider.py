import base64
import unittest
from datetime import datetime

from mail_provider import (
    GmailMailProvider, MailProviderError, MockMailProvider, MAX_BODY_CHARS,
    normalize_message, validate_request,
)

START = '2026-10-22T00:00:00-07:00'
END = '2026-10-29T00:00:00-07:00'


def raw_mail(id='a', timestamp=START, text='Hello', mime='text/plain'):
    return {
        'id': id, 'internalDate': str(int(datetime.fromisoformat(timestamp).timestamp() * 1000)),
        'payload': {
            'mimeType': mime,
            'headers': [{'name': 'Subject', 'value': 'Subject'}, {'name': 'From', 'value': 'sender@example.test'}],
            'body': {'data': base64.urlsafe_b64encode(text.encode()).decode().rstrip('=')},
        },
    }


class Request:
    def __init__(self, value):
        self.value = value

    def execute(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class FakeClient:
    def __init__(self, pages, messages):
        self.pages, self.data, self.calls = pages, messages, []

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, **kwargs):
        self.calls.append(('list', kwargs))
        return Request(self.pages[kwargs.get('pageToken')])

    def get(self, **kwargs):
        self.calls.append(('get', kwargs))
        return Request(self.data[kwargs['id']])


class ProviderTests(unittest.TestCase):
    def test_mock_range_limit_and_data_flag(self):
        result = MockMailProvider().list_emails(START, END, 2)
        self.assertTrue(result['is_mock'])
        self.assertEqual(result['count'], 2)
        self.assertTrue(result['truncated'])
        self.assertFalse(result['coverage_complete'])
        self.assertEqual(result['messages'][0]['id'], 'demo-001')

    def test_mock_empty(self):
        result = MockMailProvider([]).list_emails(START, END)
        self.assertEqual(result['messages'], [])
        self.assertTrue(result['coverage_complete'])

    def test_mock_exact_boundaries(self):
        rows = [dict(id='start', sent_at=START), dict(id='end', sent_at=END)]
        result = MockMailProvider(rows).list_emails(START, END)
        self.assertEqual([m['id'] for m in result['messages']], ['start'])

    def test_invalid_inputs(self):
        for start, end, limit in [(END, START, 1), (START, START, 1),
                                  ('2026-10-22', END, 1), ('2026-10-22T00:00:00', END, 1),
                                  (START, END, True), (START, END, 0), (START, END, 101),
                                  ('2026-99-01T00:00:00Z', END, 1)]:
            with self.subTest(start=start, end=end, limit=limit), self.assertRaises(ValueError):
                validate_request(start, end, limit)

    def test_requires_authorization_without_api_calls(self):
        client = FakeClient({}, {})
        with self.assertRaises(MailProviderError):
            GmailMailProvider(client).list_emails(START, END)
        self.assertEqual(client.calls, [])

    def test_gmail_pagination_and_exact_boundaries(self):
        client = FakeClient({None: {'messages': [{'id': 'a'}], 'nextPageToken': 'p2'},
                             'p2': {'messages': [{'id': 'b'}, {'id': 'c'}]}},
                            {'a': raw_mail('a'), 'b': raw_mail('b', END),
                             'c': raw_mail('c', '2026-10-23T00:00:00-07:00')})
        result = GmailMailProvider(client, authorized=True).list_emails(START, END)
        self.assertEqual([m['id'] for m in result['messages']], ['a', 'c'])
        self.assertTrue(result['coverage_complete'])
        self.assertFalse(result['is_mock'])
        self.assertTrue(all(args['userId'] == 'me' for _, args in client.calls))
        self.assertEqual([args['format'] for name, args in client.calls if name == 'get'], ['full'] * 3)

    def test_limit_truncates_without_claiming_full_coverage(self):
        client = FakeClient({None: {'messages': [{'id': 'a'}, {'id': 'b'}]}},
                            {'a': raw_mail('a'), 'b': raw_mail('b')})
        result = GmailMailProvider(client, authorized=True).list_emails(START, END, 1)
        self.assertEqual(result['count'], 1)
        self.assertTrue(result['has_more'])

    def test_exact_limit_can_be_complete(self):
        client = FakeClient({None: {'messages': [{'id': 'a'}]}}, {'a': raw_mail()})
        result = GmailMailProvider(client, authorized=True).list_emails(START, END, 1)
        self.assertTrue(result['coverage_complete'])

    def test_scan_limit(self):
        client = FakeClient({None: {'messages': [{'id': 'a'}], 'nextPageToken': 'next'}}, {'a': raw_mail()})
        result = GmailMailProvider(client, authorized=True, scan_limit=1).list_emails(START, END)
        self.assertTrue(result['truncated'])
        self.assertEqual(len(client.calls), 2)

    def test_error_does_not_leak_private_data(self):
        client = FakeClient({None: RuntimeError('token=SECRET private email')}, {})
        with self.assertRaises(MailProviderError) as error:
            GmailMailProvider(client, authorized=True).list_emails(START, END)
        self.assertNotIn('SECRET', str(error.exception))
        self.assertNotIn('private email', str(error.exception))

    def test_empty_gmail(self):
        self.assertEqual(GmailMailProvider(FakeClient({None: {}}, {}), authorized=True)
                         .list_emails(START, END)['count'], 0)

    def test_plain_and_html_parsing(self):
        self.assertEqual(normalize_message(raw_mail(text='你好'))['text'], '你好')
        mail = normalize_message(raw_mail(text='<style>bad</style><p>Hello &amp; hi</p><script>secret</script>', mime='text/html'))
        self.assertEqual(mail['text'], 'Hello & hi')
        self.assertIsNone(mail['source_url'])

    def test_multipart_prefers_plain_and_skips_attachments(self):
        raw = raw_mail()
        raw['payload'] = {'mimeType': 'multipart/mixed', 'parts': [
            {'mimeType': 'multipart/alternative', 'parts': [
                raw_mail(text='<p>HTML duplicate</p>', mime='text/html')['payload'],
                raw_mail(text='Plain content')['payload']]},
            dict(raw_mail(text='Attachment secret')['payload'], filename='secret.txt')]}
        self.assertEqual(normalize_message(raw)['text'], 'Plain content')

    def test_external_body_and_invalid_encoding_flagged(self):
        raw = raw_mail()
        for body in [{'attachmentId': 'body-id'}, {'data': '%%%'}]:
            raw['payload']['body'] = body
            self.assertTrue(normalize_message(raw)['body_incomplete'])

    def test_long_body_flagged(self):
        mail = normalize_message(raw_mail(text='x' * (MAX_BODY_CHARS + 1)))
        self.assertTrue(mail['body_truncated'])
        self.assertEqual(len(mail['text']), MAX_BODY_CHARS)

    def test_distinct_empty_pages_are_bounded(self):
        client = FakeClient({None: {'nextPageToken': 'one'}, 'one': {'nextPageToken': 'two'}}, {})
        result = GmailMailProvider(client, authorized=True, page_limit=2).list_emails(START, END)
        self.assertTrue(result['truncated'])
        self.assertEqual(len(client.calls), 2)

    def test_body_coverage_is_separate_from_message_coverage(self):
        client = FakeClient({None: {'messages': [{'id': 'a'}]}},
                            {'a': raw_mail(text='x' * (MAX_BODY_CHARS + 1))})
        result = GmailMailProvider(client, authorized=True).list_emails(START, END)
        self.assertTrue(result['coverage_complete'])
        self.assertEqual(result['coverage_scope'], 'message_list_only')
        self.assertFalse(result['body_content_complete'])

    def test_repeated_page_token_stops(self):
        client = FakeClient({None: {'nextPageToken': 'same'}, 'same': {'nextPageToken': 'same'}}, {})
        result = GmailMailProvider(client, authorized=True).list_emails(START, END)
        self.assertTrue(result['truncated'])
        self.assertEqual(len(client.calls), 2)


if __name__ == '__main__':
    unittest.main()
