import json
import tempfile
import unittest
from pathlib import Path
from backend.storage import Store
from backend.projects import Projects
from backend.repairs import Repairs


class FakeModel:
    def __init__(self, response):
        self.response = response
    def ask_json(self, *args):
        return self.response


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.src = root / 'source'
        self.src.mkdir()
        (self.src / 'main.ts').write_text('console.log(1)', encoding='utf-8')
        (self.src / 'tsconfig.json').write_text('{"compilerOptions":{"target":"ES0"}}', encoding='utf-8')
        (self.src / 'build.gradle').write_text("plugins { id 'java' }", encoding='utf-8')
        self.store = Store(root / 'state')
        self.projects = Projects(self.store)
        self.p = self.projects.import_path(str(self.src))
        self.store.put('runs', self.p['id'], [{'status': 'failed', 'stderr': 'invalid target', 'entry': 'main.ts'}])

    def tearDown(self):
        self.tmp.cleanup()

    def service(self, path='tsconfig.json', category='build'):
        model = FakeModel({'category': category, 'message': '修复目标版本', 'patches': [{'path': path, 'search': 'ES0', 'replacement': 'ES2020'}]})
        return Repairs(self.store, self.projects, model, lambda *args, **kwargs: {'status': 'passed', 'exit_code': 0, 'stdout': '1', 'stderr': ''})

    def test_auto_patch_and_undo_only_practice(self):
        service = self.service()
        result = service.repair(self.p['id'])
        self.assertEqual(result['status'], 'repaired')
        self.assertIn('ES2020', self.projects.read_file(self.p['id'], 'tsconfig.json')['content'])
        self.assertEqual((self.src / 'tsconfig.json').read_text(), '{"compilerOptions":{"target":"ES0"}}')
        service.undo(self.p['id'])
        self.assertEqual(self.projects.read_file(self.p['id'], 'tsconfig.json')['content'], '{"compilerOptions":{"target":"ES0"}}')

    def test_escape_and_source_patch_are_rejected(self):
        for path in ('../tsconfig.json', 'main.ts'):
            with self.assertRaises(ValueError):
                self.service(path).repair(self.p['id'])

    def test_system_change_requires_confirmation(self):
        result = self.service(category='system').repair(self.p['id'])
        self.assertEqual(result['status'], 'needs_confirmation')
        self.assertIn('ES0', self.projects.read_file(self.p['id'], 'tsconfig.json')['content'])

    def test_undo_does_not_overwrite_new_learner_work(self):
        service = self.service()
        service.repair(self.p['id'])
        self.projects.write_file(self.p['id'], 'tsconfig.json', '{"target":"ES2022"}')
        with self.assertRaisesRegex(ValueError, '冲突'):
            service.undo(self.p['id'])

    def test_executable_build_patch_cannot_auto_run(self):
        model = FakeModel({'category': 'build', 'message': 'fix', 'patches': [{'path': 'build.gradle', 'search': "plugins { id 'java' }", 'replacement': "plugins { id 'java' }\nnew File(System.getProperty('user.home'), 'marker').text = 'x'"}]})
        calls = []
        service = Repairs(self.store, self.projects, model, lambda *args: calls.append(args))
        with self.assertRaises(ValueError):
            service.repair(self.p['id'])
        self.assertEqual(calls, [])
        self.assertNotIn('marker', self.projects.read_file(self.p['id'], 'build.gradle')['content'])

    def test_json_script_changes_are_not_automatically_applied(self):
        from backend.repairs import validate_configuration_change
        with self.assertRaises(ValueError):
            validate_configuration_change('package.json', '{"scripts":{"test":"node test.js"}}', '{"scripts":{"test":"node danger.js"}}')

    def test_real_typescript_configuration_repair_and_undo(self):
        from backend.runner import run_project
        root = Path(self.p['workspace'])
        def execute(project_id, entry):
            return {**run_project(root, entry, run_key=project_id), 'entry': entry}
        first = execute(self.p['id'], 'main.ts')
        if first['status'] == 'missing_tool':
            self.skipTest(first['stderr'])
        self.assertEqual(first['status'], 'failed', first)
        self.assertIn('target', first['stdout'] + first['stderr'])
        self.store.put('runs', self.p['id'], [first])
        service = self.service()
        service.execute = execute
        result = service.repair(self.p['id'])
        self.assertEqual(result['status'], 'repaired', result)
        self.assertEqual(result['attempts'][0]['run']['stdout'].strip(), '1')
        self.assertIn('ES0', (self.src / 'tsconfig.json').read_text())
        service.undo(self.p['id'])
        self.assertEqual(execute(self.p['id'], 'main.ts')['status'], 'failed')
