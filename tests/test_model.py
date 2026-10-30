import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from backend.storage import Store
from backend.model_client import ModelClient, parse_json, redact


class ModelTests(unittest.TestCase):
    def test_persistent_credentials_are_loaded_by_new_model_without_public_exposure(self):
        with tempfile.TemporaryDirectory() as root:
            values = {}
            class Persistent:
                def __init__(self, *args, **kwargs): pass
                def read(self, account): return values.get(account), 'windows-dpapi'
                def write(self, account, value): values[account] = value; return 'windows-dpapi'
                def clear(self, account): values.pop(account, None)
            with patch('backend.model_client.CredentialStore', Persistent):
                store = Store(Path(root))
                first = ModelClient(store)
                first.configure('https://example.test/v1', 'model', 'SYNTHETIC_PERSISTENT_KEY')
                second = ModelClient(store)
                settings = second.public()
                self.assertTrue(settings['key_set'])
                self.assertEqual(settings['storage'], 'windows-dpapi')
                self.assertNotIn('SYNTHETIC_PERSISTENT_KEY', json.dumps(settings))
                self.assertNotIn('SYNTHETIC_PERSISTENT_KEY', json.dumps(store.get('settings', 'model')))
                second.configure('https://example.test/v1', 'model', generation_timeout=450)
                self.assertTrue(ModelClient(store).public()['key_set'])
                self.assertFalse(second.configure('https://other.test/v1', 'model')['key_set'])

    def test_persistence_failure_does_not_claim_saved_or_replace_settings(self):
        from backend.credentials import CredentialError
        with tempfile.TemporaryDirectory() as root, patch('backend.model_client.CredentialStore') as factory:
            factory.return_value.read.return_value = (None, 'system-keyring')
            factory.return_value.write.side_effect = CredentialError('密钥持久化失败。')
            store = Store(Path(root))
            model = ModelClient(store)
            previous = model.configure('https://example.test/v1', 'old-model')
            with self.assertRaisesRegex(ValueError, '持久化失败'):
                model.configure('https://example.test/v1', 'new-model', 'SYNTHETIC_NEW_KEY')
            self.assertEqual(model.public(), previous)

    def test_unreadable_persistent_key_is_visible_and_requests_fail_clearly(self):
        from backend.credentials import CredentialError
        with tempfile.TemporaryDirectory() as root, patch('backend.model_client.CredentialStore') as factory:
            store = Store(Path(root))
            store.put('settings', 'model', {'base_url': 'https://example.test/v1', 'model': 'model'})
            factory.return_value.read.side_effect = CredentialError('保存的密钥无法解密，请重新保存。')
            model = ModelClient(store)
            settings = model.public()
            self.assertFalse(settings['key_set'])
            self.assertEqual(settings['storage'], 'unavailable')
            self.assertIn('无法解密', settings['storage_error'])
            with self.assertRaisesRegex(ValueError, '无法解密'):
                model.chat('test', 'test')

    def test_chapter_size_limit_applies_after_exact_key_redaction_before_sending(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})
        with tempfile.TemporaryDirectory() as root:
            model = ModelClient(Store(Path(root)), use_keyring=False, transport=httpx.MockTransport(handler))
            model.configure('http://127.0.0.1:9999/v1', 'test', 'Z')
            with self.assertRaisesRegex(ValueError, '32,000'):
                model.ask_json('Return JSON', 'Z' * 4000, purpose='chapter')
            self.assertEqual(calls, [])
            self.assertEqual(model.ask_json('Return JSON', 'small request', purpose='chapter'), {})
            self.assertEqual(len(calls), 1)

    def test_generation_budget_is_persisted_and_only_applies_to_chapters(self):
        budgets = []
        def handler(request):
            budgets.append(request.extensions['timeout'])
            return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            model = ModelClient(store, use_keyring=False, transport=httpx.MockTransport(handler))
            self.assertEqual(model.public()['generation_timeout'], 300)
            model.configure('http://127.0.0.1:9999/v1', 'test', 'SYNTHETIC_KEY', generation_timeout=420)
            self.assertEqual(model.configure('http://127.0.0.1:9999/v1', 'test')['generation_timeout'], 420)
            self.assertEqual(ModelClient(store, use_keyring=False).public()['generation_timeout'], 420)
            model.ask_json('system', 'user', purpose='chapter')
            model.chat('system', 'user', purpose='connectivity')
            model.chat('system', 'user')
            self.assertEqual([b['read'] for b in budgets], [420, 30, 90])
            self.assertTrue(all(b['connect'] == 15 for b in budgets))
            self.assertTrue(model.public()['key_set'])
            for invalid in (0, 29, 901, 1.5, True):
                with self.assertRaises(ValueError):
                    model.configure('http://127.0.0.1:9999/v1', 'test', generation_timeout=invalid)

    def test_timeout_is_classified_logged_without_secrets_and_never_retried(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            calls = []
            for error_type, message in [(httpx.ConnectTimeout, '连接'), (httpx.ReadTimeout, '300'), (httpx.WriteTimeout, '发送'), (httpx.PoolTimeout, '连接池')]:
                def handler(request):
                    calls.append(request)
                    raise error_type('SYNTHETIC_SECRET and private source', request=request)
                model = ModelClient(store, use_keyring=False, transport=httpx.MockTransport(handler))
                model.configure('https://example.test/v1', 'test', 'SYNTHETIC_SECRET')
                with self.assertRaisesRegex(ValueError, message):
                    model.ask_json('private system', 'private source', purpose='chapter')
                record = store.get('model_requests', 'recent')[0]
                self.assertEqual(record['exception_type'], error_type.__name__)
                self.assertEqual(record['purpose'], 'chapter')
                self.assertGreater(record['request_bytes'], 0)
                self.assertGreaterEqual(record['duration_seconds'], 0)
                self.assertNotIn('SYNTHETIC_SECRET', json.dumps(record))
                self.assertNotIn('private', json.dumps(record))
            self.assertEqual(len(calls), 4)

    def test_json_fences_and_invalid_content(self):
        self.assertEqual(parse_json('```json\n{"a":1}\n```'), {'a': 1})
        with self.assertRaises(ValueError):
            parse_json('I cannot produce JSON')

    def test_settings_never_return_key_and_endpoint_change_clears_key(self):
        with tempfile.TemporaryDirectory() as root:
            model = ModelClient(Store(Path(root)), use_keyring=False)
            result = model.configure('https://example.test/v1', 'model', 'synthetic-test-key')
            self.assertNotIn('synthetic-test-key', json.dumps(result))
            self.assertTrue(result['key_set'])
            self.assertFalse(model.configure('https://other.test/v1', 'model')['key_set'])

    def test_compatible_payload_and_response(self):
        def handler(request):
            body = json.loads(request.content)
            self.assertEqual(request.url.path, '/v1/chat/completions')
            self.assertEqual(body['model'], 'local-test')
            self.assertEqual(body['messages'][-1]['content'], 'hello')
            return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok":true}'}}]})
        with tempfile.TemporaryDirectory() as root:
            model = ModelClient(Store(Path(root)), use_keyring=False, transport=httpx.MockTransport(handler))
            model.configure('http://127.0.0.1:9999/v1', 'local-test', 'synthetic-test-key')
            self.assertEqual(model.ask_json('system', 'hello'), {'ok': True})

    def test_remote_plain_http_rejected_and_missing_settings_are_clear(self):
        with tempfile.TemporaryDirectory() as root:
            model = ModelClient(Store(Path(root)), use_keyring=False)
            with self.assertRaises(ValueError):
                model.configure('http://example.com/v1', 'x')
            with self.assertRaisesRegex(ValueError, '配置'):
                model.ask_json('sys', 'hello')

    def test_log_secrets_are_redacted(self):
        result = redact('API_KEY=abc123 password: hunter2 Bearer token-123')
        self.assertNotIn('abc123', result)
        self.assertNotIn('hunter2', result)
        self.assertNotIn('token-123', result)

    def test_quoted_assignments_and_json_credentials_are_masked(self):
        cases = [
            ('password = "SYNTHETIC_PASSWORD"', 'SYNTHETIC_PASSWORD'),
            ("secret = 'SYNTHETIC_SECRET'", 'SYNTHETIC_SECRET'),
            ('{"api_key":"SYNTHETIC_API_TOKEN","stock":10}', 'SYNTHETIC_API_TOKEN'),
            ('DB_PASSWORD=SYNTHETIC_DATABASE_SECRET', 'SYNTHETIC_DATABASE_SECRET'),
            ('const accessToken = "SYNTHETIC_ACCESS_TOKEN";', 'SYNTHETIC_ACCESS_TOKEN'),
            ('token: SYNTHETIC_TOKEN', 'SYNTHETIC_TOKEN'),
            ('Authorization: Bearer SYNTHETIC_UNQUOTED_AUTH', 'SYNTHETIC_UNQUOTED_AUTH'),
            ('const apiKey: string = "SYNTHETIC_TYPED_SECRET";', 'SYNTHETIC_TYPED_SECRET'),
            ('Authorization: "Bearer SYNTHETIC_AUTH_TOKEN"', 'SYNTHETIC_AUTH_TOKEN'),
            (json.dumps({"client_secret": 'contains spaces and "quotes"'}), "contains spaces"),
        ]
        for source, credential in cases:
            with self.subTest(source=source):
                masked = redact(source)
                self.assertNotIn(credential, masked)
                self.assertIn('[REDACTED]', masked)
        masked = redact('{"api_key":"SYNTHETIC_API_TOKEN","stock":10}')
        self.assertEqual(json.loads(masked), {'api_key': '[REDACTED]', 'stock': 10})

    def test_redaction_preserves_ordinary_learning_expressions(self):
        source = """stock = 10
ordered = 2
password_length = 12
token_count = stock - ordered
secret_count = 3
secret = load_secret()
password = input()
api_key = os.getenv("APP_KEY")
remaining = stock - ordered
"""
        self.assertEqual(redact(source), source)
        self.assertEqual(redact('value=EXPLICIT_SYNTHETIC_VALUE', ['EXPLICIT_SYNTHETIC_VALUE']), 'value=[REDACTED]')

    def test_known_standalone_credentials_and_private_blocks(self):
        credentials = ['sk-proj-SYNTHETICabcdefgh123456', 'ghp_' + 'S' * 36, 'github_pat_' + 'S' * 40,
                       'AKIA' + 'S' * 16, 'xoxb-1234567890-SYNTHETIC',
                       'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJTWU5USEVUSUMifQ.SYNTHETICSIGNATURE1234']
        for credential in credentials:
            self.assertNotIn(credential, redact('log: ' + credential))
        block = '-----BEGIN PRIVATE KEY-----\nSYNTHETIC_NOT_A_REAL_KEY\n-----END PRIVATE KEY-----'
        self.assertNotIn('SYNTHETIC_NOT_A_REAL_KEY', redact(block))
        self.assertNotIn('SYNTHETIC_NOT_A_REAL_KEY', redact(block.split('-----END')[0]))

    def test_request_payload_masks_source_credentials_but_keeps_auth_header(self):
        seen = []
        def handler(request):
            body = json.loads(request.content)
            seen.append(body)
            for message in body['messages']:
                self.assertNotIn('SYNTHETIC_SOURCE_PASSWORD', message['content'])
                self.assertNotIn('SYNTHETIC_SOURCE_TOKEN', message['content'])
                self.assertNotIn('SYNTHETIC_CONFIGURED_KEY', message['content'])
            self.assertEqual(request.headers['Authorization'], 'Bearer SYNTHETIC_CONFIGURED_KEY')
            self.assertIn('remaining = stock - ordered', body['messages'][1]['content'])
            return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})
        with tempfile.TemporaryDirectory() as root:
            model = ModelClient(Store(Path(root)), use_keyring=False, transport=httpx.MockTransport(handler))
            model.configure('http://127.0.0.1:9999/v1', 'local-test', 'SYNTHETIC_CONFIGURED_KEY')
            model.chat('Check password = "SYNTHETIC_SOURCE_PASSWORD"',
                       '{"api_key":"SYNTHETIC_SOURCE_TOKEN"}\nremaining = stock - ordered\nSYNTHETIC_CONFIGURED_KEY')
            self.assertEqual(len(seen), 1)
