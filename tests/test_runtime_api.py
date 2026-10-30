import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.api import create_app


class RuntimeApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(Path(self.temp.name) / 'state', use_keyring=False)
        self.client = TestClient(self.app, base_url='http://127.0.0.1', client=('127.0.0.1', 45000))
        self.addCleanup(self.client.close)
        self.headers = {'X-Codewarden-Runtime-Token': 'synthetic-runtime-token'}
        self.info = {'schema': 1, 'project_root': 'synthetic-project', 'data_root': str(Path(self.temp.name) / 'state'), 'pid': 123, 'port': 8766, 'instance_id': 'synthetic-instance', 'started_at': 'synthetic-time'}
        self.stopped = []

    def register(self):
        self.app.state.runtime_info = self.info
        self.app.state.runtime_token = 'synthetic-runtime-token'
        self.app.state.shutdown_callback = lambda: self.stopped.append(True)

    def test_unmanaged_instance_rejects_control(self):
        self.assertEqual(self.client.get('/api/runtime', headers=self.headers).status_code, 404)
        self.assertEqual(self.client.post('/api/runtime/shutdown', headers=self.headers, json={}).status_code, 404)

    def test_authenticated_loopback_control_never_returns_token(self):
        self.register()
        self.assertEqual(self.client.get('/api/runtime').status_code, 403)
        self.assertEqual(self.client.post('/api/runtime/shutdown', json={}).status_code, 403)
        response = self.client.get('/api/runtime', headers=self.headers)
        self.assertEqual(response.json(), self.info)
        self.assertNotIn('synthetic-runtime-token', response.text)
        remote = TestClient(self.app, base_url='http://127.0.0.1', client=('192.0.2.1', 45000))
        self.addCleanup(remote.close)
        self.assertEqual(remote.get('/api/runtime', headers=self.headers).status_code, 403)
        response = self.client.post('/api/runtime/shutdown', headers=self.headers, json={})
        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()['stopping'])
        self.assertEqual(self.stopped, [True])

    def test_busy_generation_blocks_restart_and_shutdown_blocks_new_work(self):
        from backend.campaigns import Campaigns
        self.register()
        source = Path(self.temp.name) / 'source'
        source.mkdir()
        (source / 'main.py').write_text('print(1)\n', encoding='utf-8')
        project = self.client.post('/api/projects/import', json={'path': str(source)}).json()
        base = '/api/projects/' + project['id']
        started, finish = Event(), Event()
        def delayed(*args, **kwargs):
            started.set()
            finish.wait(10)
            return {'status': 'failed'}
        with patch.object(Campaigns, 'generate', delayed), ThreadPoolExecutor(max_workers=1) as pool:
            request = pool.submit(self.client.post, base + '/campaign', json={'role': 'mage'})
            try:
                self.assertTrue(started.wait(5))
                self.assertEqual(self.client.post('/api/runtime/shutdown', headers=self.headers, json={}).status_code, 409)
                self.assertEqual(self.stopped, [])
            finally:
                finish.set()
            request.result()
            self.assertEqual(self.client.post('/api/runtime/shutdown', headers=self.headers, json={}).status_code, 202)
            self.assertEqual(self.client.post(base + '/campaign', json={'role': 'mage'}).status_code, 503)
