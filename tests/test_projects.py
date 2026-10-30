import tempfile
import unittest
from pathlib import Path

from backend.storage import Store
from backend.projects import Projects


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / '源项目 with spaces'
        self.source.mkdir()
        (self.source / 'main.py').write_text('print(1)\n', encoding='utf-8')
        (self.source / '.env').write_text('SECRET=do-not-copy', encoding='utf-8')
        self.store = Store(self.root / 'state')
        self.projects = Projects(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_import_copies_source_and_skips_secrets(self):
        project = self.projects.import_path(str(self.source))
        workspace = Path(project['workspace'])
        self.assertEqual((workspace / 'main.py').read_text(), 'print(1)\n')
        self.assertFalse((workspace / '.env').exists())
        self.assertEqual(project['languages'], ['python'])

    def test_edit_never_changes_original_and_conflict_is_rejected(self):
        project = self.projects.import_path(str(self.source))
        file = self.projects.read_file(project['id'], 'main.py')
        self.projects.write_file(project['id'], 'main.py', 'print(2)\n', file['hash'])
        self.assertEqual((self.source / 'main.py').read_text(), 'print(1)\n')
        with self.assertRaisesRegex(ValueError, '冲突'):
            self.projects.write_file(project['id'], 'main.py', 'print(3)', file['hash'])

    def test_traversal_cannot_read_or_write(self):
        project = self.projects.import_path(str(self.source))
        for name in ('../outside.py', '/etc/passwd', 'C:\\outside.py'):
            with self.assertRaises(ValueError):
                self.projects.read_file(project['id'], name)

    def test_progress_survives_store_reopen(self):
        project = self.projects.import_path(str(self.source))
        self.store.put('quests', project['id'], [{'id': 'q1', 'status': 'completed'}])
        other = Projects(Store(self.root / 'state'))
        self.assertEqual(other.get(project['id'])['completed'], 1)

    def test_original_change_marks_stale_without_overwriting_edits(self):
        project = self.projects.import_path(str(self.source))
        self.projects.write_file(project['id'], 'main.py', 'print(2)', None)
        (self.source / 'main.py').write_text('print(3)', encoding='utf-8')
        self.assertTrue(self.projects.refresh(project['id'])['stale'])
        self.assertEqual(self.projects.read_file(project['id'], 'main.py')['content'], 'print(2)')


if __name__ == '__main__':
    unittest.main()
