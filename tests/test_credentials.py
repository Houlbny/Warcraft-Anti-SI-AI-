import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.credentials import CredentialError, CredentialStore


class MissingPassword(Exception):
    pass


class FakeKeyring:
    class errors:
        PasswordDeleteError = MissingPassword
    def __init__(self):
        self.values = {}
        self.fail_write = self.fail_read = self.fail_delete = False
        self.read_calls = []
    def get_password(self, service, account):
        self.read_calls.append((service, account))
        if self.fail_read:
            raise RuntimeError('synthetic unavailable')
        return self.values.get((service, account))
    def set_password(self, service, account, value):
        if self.fail_write:
            raise RuntimeError('synthetic unavailable')
        self.values[(service, account)] = value
    def delete_password(self, service, account):
        if self.fail_delete:
            raise RuntimeError('synthetic unavailable')
        if (service, account) not in self.values:
            raise MissingPassword()
        del self.values[(service, account)]


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.account = hashlib.sha256((str(self.root) + 'synthetic.invalid').encode()).hexdigest()
        self.keyring = FakeKeyring()
        self.mock_keyring = patch('backend.credentials._keyring_module', return_value=self.keyring)
        self.mock_keyring.start()
    def tearDown(self):
        self.mock_keyring.stop()
        self.temp.cleanup()
    def store(self, enabled=True):
        return CredentialStore(self.root, enabled=enabled)
    def fake_windows(self):
        # Only a logic fixture; the separate native test exercises actual DPAPI.
        return patch('backend.credentials.sys.platform', 'win32')
    def protect(self):
        return patch('backend.credentials._protect', side_effect=lambda data, entropy: b'fixture:' + bytes(x ^ 165 for x in data))
    def unprotect(self):
        return patch('backend.credentials._unprotect', side_effect=lambda data, entropy: bytes(x ^ 165 for x in data[len(b'fixture:'):]))
    def test_keyring_new_instance_update_delete_and_no_plaintext_files(self):
        first = 'synthetic-key-alpha-uniqueness'
        self.assertEqual(self.store().write(self.account, first), 'system-keyring')
        self.assertEqual(self.store().read(self.account), (first, 'system-keyring'))
        self.store().write(self.account, 'synthetic-key-beta')
        self.assertEqual(self.store().read(self.account)[0], 'synthetic-key-beta')
        for file in self.root.rglob('*'):
            if file.is_file():
                self.assertNotIn(first.encode(), file.read_bytes())
        self.store().clear(self.account)
        self.assertIsNone(self.store().read(self.account)[0])
    def test_windows_fallback_is_persisted_and_old_backend_never_revives(self):
        self.store().write(self.account, 'synthetic-old-keyring')
        self.keyring.values[('codewarden', self.account)] = 'synthetic-legacy-old'
        self.keyring.fail_write = True
        with self.fake_windows(), self.protect(), self.unprotect():
            self.assertEqual(self.store().write(self.account, 'synthetic-new-dpapi'), 'windows-dpapi')
            self.assertEqual(self.store().read(self.account), ('synthetic-new-dpapi', 'windows-dpapi'))
            reads = len(self.keyring.read_calls)
            self.keyring.fail_write = False
            self.store().write(self.account, 'synthetic-updated-dpapi')
            self.assertEqual(self.store().read(self.account)[0], 'synthetic-updated-dpapi')
            self.assertEqual(len(self.keyring.read_calls), reads)
            self.store().clear(self.account)
            self.assertIsNone(self.store().read(self.account)[0])
            self.assertEqual(len(self.keyring.read_calls), reads)
    def test_legacy_known_account_migrates_without_enumeration(self):
        self.keyring.values[('codewarden', self.account)] = 'synthetic-legacy-value'
        self.assertEqual(self.store().read(self.account), ('synthetic-legacy-value', 'system-keyring'))
        meta = json.loads((self.root/'credentials'/(self.account+'.json')).read_text())
        self.assertTrue(meta['service'].startswith('codewarden:' + self.account + ':'))
        self.assertIn((meta['service'], self.account), self.keyring.values)
        self.keyring.values[('codewarden', self.account)] = 'synthetic-stale-legacy'
        self.assertEqual(self.store().read(self.account)[0], 'synthetic-legacy-value')
    def test_fixed_service_metadata_remains_readable_during_upgrade(self):
        service = 'codewarden:' + self.account
        self.keyring.values[(service, self.account)] = 'synthetic-fixed-committed'
        directory = self.root/'credentials'
        directory.mkdir()
        (directory/(self.account+'.json')).write_text(json.dumps({'version':1,'state':'active','storage':'system-keyring','service':service}))
        self.assertEqual(self.store().read(self.account), ('synthetic-fixed-committed', 'system-keyring'))
        self.store().write(self.account, 'synthetic-versioned-upgrade')
        self.assertEqual(self.store().read(self.account)[0], 'synthetic-versioned-upgrade')

    def test_clear_failure_is_visible_and_tombstone_prevents_revival(self):
        self.store().write(self.account, 'synthetic-to-clear')
        self.keyring.fail_delete = True
        with self.assertRaises(CredentialError):
            self.store().clear(self.account)
        with self.assertRaises(CredentialError):
            self.store().read(self.account)
        self.keyring.fail_delete = False
        self.store().clear(self.account)
        self.assertIsNone(self.store().read(self.account)[0])
    def test_unknown_account_clear_succeeds_with_unavailable_vault_and_prevents_legacy_revival(self):
        self.keyring.fail_delete = self.keyring.fail_read = True
        self.store().clear(self.account)
        self.keyring.fail_delete = self.keyring.fail_read = False
        self.keyring.values[('codewarden', self.account)] = 'synthetic-inaccessible-stale'
        self.assertIsNone(self.store().read(self.account)[0])

    def test_keyring_update_commit_failure_keeps_previous_value_even_if_orphan_cleanup_fails(self):
        self.store().write(self.account, 'synthetic-committed-old')
        store = self.store()
        self.keyring.fail_delete = True
        with patch.object(store, '_commit', side_effect=CredentialError('synthetic metadata failure')):
            with self.assertRaises(CredentialError):
                store.write(self.account, 'synthetic-uncommitted-new')
        self.assertEqual(self.store().read(self.account), ('synthetic-committed-old', 'system-keyring'))

    def test_first_keyring_commit_failure_does_not_discover_orphaned_version(self):
        store = self.store()
        self.keyring.fail_delete = True
        with patch.object(store, '_commit', side_effect=CredentialError('synthetic metadata failure')):
            with self.assertRaises(CredentialError):
                store.write(self.account, 'synthetic-orphan-first')
        self.assertEqual(self.store().read(self.account), (None, 'system-keyring'))

    def test_failed_backend_reference_commit_preserves_previous_authority(self):
        self.store().write(self.account, 'synthetic-previous-committed')
        self.keyring.fail_write = True
        store = self.store()
        with self.fake_windows(), self.protect(), self.unprotect(), patch.object(store, '_commit', side_effect=CredentialError('synthetic disk failure')):
            with self.assertRaises(CredentialError):
                store.write(self.account, 'synthetic-not-committed')
        self.assertEqual(self.store().read(self.account), ('synthetic-previous-committed', 'system-keyring'))
        self.assertEqual(list((self.root/'credentials').glob('*.bin')), [])

    def test_dpapi_clear_failure_is_reported_and_never_falls_back_to_old_keyring(self):
        self.keyring.fail_write = True
        with self.fake_windows(), self.protect(), self.unprotect():
            store = self.store()
            store.write(self.account, 'synthetic-encrypted-to-clear')
            with patch('backend.credentials.Path.unlink', side_effect=PermissionError('synthetic denied')):
                with self.assertRaises(CredentialError):
                    store.clear(self.account)
            self.keyring.values[('codewarden', self.account)] = 'synthetic-obsolete'
            with self.assertRaises(CredentialError):
                self.store().read(self.account)
            store.clear(self.account)
            self.assertIsNone(self.store().read(self.account)[0])

    def test_corrupt_metadata_is_actionable_and_explicit_replacement_recovers(self):
        self.store().write(self.account, 'synthetic-first')
        (self.root / 'credentials' / (self.account + '.json')).write_text('{broken', encoding='utf-8')
        with self.assertRaises(CredentialError) as error:
            self.store().read(self.account)
        self.assertNotIn('synthetic-first', str(error.exception))
        self.store().write(self.account, 'synthetic-replacement')
        self.assertEqual(self.store().read(self.account)[0], 'synthetic-replacement')
    def test_no_session_fallback_on_real_persistence_failure(self):
        self.keyring.fail_write = True
        with patch('backend.credentials.sys.platform', 'darwin'):
            with self.assertRaises(CredentialError):
                self.store().write(self.account, 'synthetic-not-saved')
        with self.fake_windows(), patch('backend.credentials._protect', side_effect=CredentialError('synthetic crypto failure')):
            with self.assertRaises(CredentialError):
                self.store().write(self.account, 'synthetic-not-saved')
    def test_disabled_only_explicit_session_and_path_validation(self):
        store = self.store(False)
        self.assertEqual(store.write(self.account, 'synthetic-session'), 'session')
        self.assertEqual(store.read(self.account), ('synthetic-session', 'session'))
        self.assertEqual(self.store(False).read(self.account), (None, 'session'))
        self.assertFalse((self.root / 'credentials').exists())
        for account in ('../escape', 'bad', 'a'*63+'/', ''):
            with self.assertRaises(CredentialError):
                store.write(account, 'synthetic')
    def test_metadata_never_follows_arbitrary_blob_path(self):
        directory = self.root / 'credentials'
        directory.mkdir()
        (directory / (self.account + '.json')).write_text(json.dumps({'version':1,'state':'active','storage':'windows-dpapi','blob':'../outside.bin'}))
        with self.assertRaises(CredentialError):
            self.store().read(self.account)
    @unittest.skipUnless(sys.platform == 'win32', 'Native DPAPI requires Windows current-user context')
    def test_native_dpapi_cross_process_update_clear_corruption_and_no_plaintext(self):
        secret = 'synthetic-native-DPAPI-密钥-unique'
        self.keyring.fail_write = True
        self.assertEqual(self.store().write(self.account, secret), 'windows-dpapi')
        self.assertEqual(self.store().read(self.account)[0], secret)
        code = "from backend.credentials import CredentialStore; import sys,json; print(json.dumps(CredentialStore(sys.argv[1]).read(sys.argv[2]),ensure_ascii=True))"
        result = subprocess.run([sys.executable, '-c', code, str(self.root), self.account], capture_output=True, text=True, check=True, cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(json.loads(result.stdout), [secret, 'windows-dpapi'])
        for file in (self.root / 'credentials').iterdir():
            self.assertNotIn(secret.encode(), file.read_bytes())
        self.store().write(self.account, 'synthetic-native-updated')
        self.assertEqual(self.store().read(self.account)[0], 'synthetic-native-updated')
        meta=json.loads((self.root/'credentials'/(self.account+'.json')).read_text())
        (self.root/'credentials'/meta['blob']).write_bytes(b'corrupt-not-dpapi')
        with self.assertRaises(CredentialError):
            self.store().read(self.account)
        self.store().write(self.account, 'synthetic-native-recovered')
        self.store().clear(self.account)
        result = subprocess.run([sys.executable, '-c', code, str(self.root), self.account], capture_output=True, text=True, check=True, cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(json.loads(result.stdout), [None, 'windows-dpapi'])


if __name__ == '__main__':
    unittest.main()
