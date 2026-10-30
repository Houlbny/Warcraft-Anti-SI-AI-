import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from backend.api import create_app


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(Path(self.temp.name), use_keyring=False))

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_demo_progress_and_original_protection(self):
        result = self.client.post('/api/projects/demo', json={'language': 'python'})
        self.assertEqual(result.status_code, 200, result.text)
        project = result.json()
        base = '/api/projects/' + project['id']
        quests = self.client.get(base + '/quests').json()
        self.assertNotIn('answer', quests[0])
        answer = self.client.post(base + '/quests/' + quests[0]['id'] + '/answer', json={'answer': 'remaining'}).json()
        self.assertTrue(answer['correct'])
        repair = next(q for q in quests if q['type'] == 'repair')
        wrong = self.client.post(base + '/run', json={'quest_id': repair['id']}).json()
        self.assertFalse(wrong.get('quest_completed', False))
        file = self.client.get(base + '/file', params={'path': 'main.py'}).json()
        updated = file['content'].replace('stock + ordered', 'stock - ordered')
        self.assertNotEqual(file['content'], updated)
        saved = self.client.put(base + '/file', json={'path': 'main.py', 'content': updated, 'expected_hash': file['hash']})
        self.assertEqual(saved.status_code, 200, saved.text)
        run = self.client.post(base + '/run', json={'quest_id': repair['id']})
        self.assertEqual(run.status_code, 200, run.text)
        self.assertTrue(run.json()['quest_completed'], run.text)
        current = self.client.get(base + '/file', params={'path': 'main.py'}).json()
        self.assertEqual(current['original'], file['content'])
        self.assertEqual(self.client.get(base).json()['completed'], 2)

    def test_model_missing_and_cross_origin_write_fail_clearly(self):
        self.assertEqual(self.client.post('/api/settings/test').status_code, 400)
        response = self.client.post('/api/projects/demo', json={'language': 'python'}, headers={'Origin': 'https://attacker.example'})
        self.assertEqual(response.status_code, 403)

    def test_offline_library_favorites(self):
        cards = self.client.get('/api/knowledge', params={'language': 'go'}).json()
        self.assertGreaterEqual(len(cards), 6)
        self.client.put('/api/knowledge/' + cards[0]['id'] + '/favorite', json={'favorite': True})
        reread = self.client.get('/api/knowledge', params={'language': 'go'}).json()
        self.assertTrue(next(c for c in reread if c['id'] == cards[0]['id'])['favorite'])

    def test_unknown_project_and_bad_path(self):
        self.assertEqual(self.client.get('/api/projects/missing').status_code, 404)
        result = self.client.post('/api/projects/import', json={'path': '/nonexistent-123'})
        self.assertEqual(result.status_code, 400)

    def test_own_origin_on_alternate_port(self):
        response = self.client.post('http://localhost:8766/api/projects/demo', json={'language': 'python'}, headers={'Origin': 'http://localhost:8766'})
        self.assertEqual(response.status_code, 200)

    def test_unrelated_entry_cannot_complete_quest(self):
        project = self.client.post('/api/projects/demo', json={'language': 'python'}).json()
        response = self.client.post('/api/projects/' + project['id'] + '/run', json={'quest_id': 'python-repair', 'entry': 'README.md'})
        self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
