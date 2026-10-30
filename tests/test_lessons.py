import tempfile
import unittest
from pathlib import Path
from backend.storage import Store
from backend.projects import Projects
from backend.lessons import Lessons


class FakeModel:
    def __init__(self, result):
        self.result = result
    def ask_json(self, *args):
        return self.result


class LessonTests(unittest.TestCase):
    def test_generated_source_must_exist(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            source = path / 'source'
            source.mkdir()
            (source / 'main.py').write_text('print(1)', encoding='utf-8')
            store = Store(path / 'state')
            projects = Projects(store)
            project = projects.import_path(str(source))
            model = FakeModel({'quests': [{'type': 'explore', 'title': 'bad', 'brief': 'x', 'objective': 'x', 'source_file': '../../secret', 'line': 1, 'answer': 'x'}]})
            with self.assertRaises(ValueError):
                Lessons(store, projects, model).generate(project['id'])
            self.assertEqual(store.get('quests', project['id'], []), [])

    def test_no_text_answer_completes_code_task(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            store.put('quests', 'p', [{'id': 'q', 'type': 'repair', 'status': 'available'}])
            with self.assertRaises(ValueError):
                Lessons(store, None, None).answer('p', 'q', 'I fixed it')

    def test_unrelated_run_cannot_satisfy_task(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            store.put('quests', 'p', [{'id': 'q', 'type': 'repair', 'source_file': 'broken.py', 'expected_output': '8', 'status': 'available'}])
            lessons = Lessons(store, None, None)
            self.assertFalse(lessons.grade_run('p', 'q', {'status': 'passed', 'exit_code': 0, 'stdout': '8', 'entry': 'unrelated.py'}))
            self.assertEqual(store.get('quests', 'p')[0]['status'], 'available')
